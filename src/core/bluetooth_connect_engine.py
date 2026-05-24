from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Protocol

from src.core.bluetooth_pairing import BluetoothActionResult, BluetoothManager
from src.core.types import BluetoothConnectCycleResult, BluetoothConnectSettings, BluetoothSwitchSettings, RunSummary

_LOGGER = logging.getLogger("bluetooth.connect_engine")


class RelayController(Protocol):
    def query_channel_state(self, channel: int) -> bool: ...

    def set_channel_state(self, channel: int, on: bool) -> None: ...


class StopRequested(Exception):
    pass


class NonRecoverableError(Exception):
    pass


class BluetoothConnectRunner:
    def __init__(
        self,
        relay: RelayController,
        bluetooth: BluetoothManager,
        settings: BluetoothConnectSettings | BluetoothSwitchSettings,
        log_cb: Callable[[str, str], None] | None = None,
        progress_cb: Callable[[int, int], None] | None = None,
        cycle_cb: Callable[[BluetoothConnectCycleResult], None] | None = None,
    ) -> None:
        self._relay = relay
        self._bluetooth = bluetooth
        self._settings = settings
        self._log_cb = log_cb
        self._progress_cb = progress_cb
        self._cycle_cb = cycle_cb
        self._stop_flag = threading.Event()
        self._skip_relay_state_query = False

    def stop(self) -> None:
        self._stop_flag.set()

    def run(self) -> RunSummary:
        success_count = 0
        fail_count = 0
        total = max(0, self._settings.test_count)

        self._log("INFO", f"蓝牙连接测试开始，总轮次: {total}")
        if total > 0:
            try:
                self._ensure_preconditions()
            except StopRequested:
                self._log("WARNING", "收到停止请求，测试未进入循环。")
                total = 0
            except NonRecoverableError as exc:
                self._log("ERROR", f"前置条件不满足，测试未启动: {exc}")
                total = 0

        for index in range(1, total + 1):
            if self._stop_flag.is_set():
                self._log("WARNING", "检测到停止请求，测试提前结束。")
                break

            try:
                result = self._run_single_cycle(index)
            except StopRequested:
                self._log("WARNING", "收到停止请求，退出测试循环。")
                break
            except NonRecoverableError as exc:
                self._log("ERROR", f"不可恢复错误，测试终止: {exc}")
                break
            except Exception as exc:  # noqa: BLE001
                self._log("ERROR", f"单轮执行异常，记失败并继续: {exc}")
                result = BluetoothConnectCycleResult(
                    index=index,
                    success=False,
                    reason=f"执行异常: {exc}",
                )

            if result.success:
                success_count += 1
            else:
                fail_count += 1
            self._emit_cycle(result)
            self._emit_progress(success_count + fail_count, total)

        done = success_count + fail_count
        success_rate = (success_count / done * 100.0) if done else 0.0
        summary = RunSummary(
            success_count=success_count,
            fail_count=fail_count,
            success_rate=success_rate,
        )
        self._log(
            "INFO",
            f"蓝牙连接测试结束：成功 {success_count}，失败 {fail_count}，成功率 {success_rate:.2f}%",
        )
        return summary

    def _ensure_preconditions(self) -> None:
        self._ensure_mode_channel_on(None)
        connected, matched = self._check_target_connected_once("测试前置检查")
        if connected:
            self._log(
                "INFO",
                f"测试前置检查通过：目标蓝牙设备已配对并连接，匹配到 {len(matched)} 个设备。",
            )
            return

        paired = self._bluetooth.query_paired_devices(
            self._settings.bt_name_keyword,
            self._settings.bt_mac,
            self._settings.bt_match_mode,
        )
        if paired:
            self._log(
                "WARNING",
                "测试前置检查：目标蓝牙设备已配对但未连接，将先删除旧配对并重新连接。",
            )
            remove_result = self._remove_target_device(None, phase="前置条件修复")
            if not remove_result.ok:
                raise NonRecoverableError(
                    f"目标蓝牙设备未连接，且删除旧配对失败: {remove_result.reason}"
                )
        else:
            self._log(
                "WARNING",
                "测试前置检查：未检测到目标蓝牙设备处于已配对连接状态，将先执行设备连接。",
            )

        pair_result = self._pair_target_device(None, phase="前置条件修复")
        if not pair_result.ok:
            raise NonRecoverableError(f"前置条件自动连接失败: {pair_result.reason}")

        connected, matched = self._wait_for_target_connected("前置条件连接后检查")
        if not connected:
            raise NonRecoverableError("前置条件自动连接完成，但仍未检测到目标蓝牙设备已连接。")
        self._log(
            "INFO",
            f"前置条件自动连接成功：匹配到 {len(matched)} 个设备，开始正式循环测试。",
        )

    def _run_single_cycle(self, index: int) -> BluetoothConnectCycleResult:
        settings = self._settings
        paired_before = self._bluetooth.query_paired_devices(
            settings.bt_name_keyword,
            settings.bt_mac,
            settings.bt_match_mode,
        )
        if paired_before:
            self._log("INFO", f"[第{index}轮] 检测到 {len(paired_before)} 个已配对目标设备。")
        else:
            self._log("WARNING", f"[第{index}轮] 删除前未检测到已配对目标设备，将继续尝试添加。")

        remove_before = self._remove_target_device(index, phase="本轮删除设备")
        if not remove_before.ok:
            return BluetoothConnectCycleResult(
                index=index,
                success=False,
                reason=f"删除设备失败: {remove_before.reason}",
                paired_before_cycle=bool(paired_before),
                removed_before_cycle=False,
            )

        pair_result = self._pair_target_device(index, phase="本轮添加设备")
        if not pair_result.ok:
            return BluetoothConnectCycleResult(
                index=index,
                success=False,
                reason=f"自动配对失败: {pair_result.reason}",
                paired_before_cycle=bool(paired_before),
                removed_before_cycle=True,
                paired_after_pairing=bool(pair_result.matched),
                connected_after_pairing=False,
            )

        connected_after_pairing, matched_after_pairing = self._wait_for_target_connected(
            f"第{index}轮添加后连接检查"
        )
        if not connected_after_pairing:
            return BluetoothConnectCycleResult(
                index=index,
                success=False,
                reason="添加设备完成，但超时未检测到目标蓝牙设备已连接。",
                paired_before_cycle=bool(paired_before),
                removed_before_cycle=True,
                paired_after_pairing=bool(pair_result.matched or matched_after_pairing),
                connected_after_pairing=False,
            )

        return BluetoothConnectCycleResult(
            index=index,
            success=True,
            reason="删除设备、重新添加并连接成功",
            paired_before_cycle=bool(paired_before),
            removed_before_cycle=True,
            paired_after_pairing=True,
            connected_after_pairing=True,
            removed_after_cycle=False,
        )

    def _ensure_mode_channel_on(self, index: int | None) -> None:
        channel = self._settings.mode_relay_channel
        prefix = "[测试前置]" if index is None else f"[第{index}轮]"
        current_state: bool | None = None
        if not self._skip_relay_state_query:
            try:
                current_state = self._relay.query_channel_state(channel)
            except Exception as exc:
                self._skip_relay_state_query = True
                self._log(
                    "WARNING",
                    f"{prefix} 读取蓝牙模式通道{channel}状态失败，将直接下发打开命令: {exc}",
                )

        if current_state:
            self._log("INFO", f"{prefix} 蓝牙模式通道{channel}已处于打开状态。")
            return
        self._set_channel_state(channel, True, f"{prefix} 切换鼠标到蓝牙模式")
        self._controlled_sleep(0.1)

    def _trigger_pairing_mode(self, index: int | None) -> None:
        channel = self._settings.pairing_relay_channel
        prefix = "[前置条件]" if index is None else f"[第{index}轮]"
        hold_seconds = max(0.05, self._settings.pairing_press_ms / 1000.0)
        self._log(
            "INFO",
            f"{prefix} 触发配对模式：打开配对通道{channel}，持续 {hold_seconds:.3f}s。",
        )
        self._set_channel_state(channel, True, f"{prefix} 打开配对按键通道")
        try:
            self._controlled_sleep(hold_seconds)
        finally:
            self._set_channel_state(channel, False, f"{prefix} 关闭配对按键通道")
        self._controlled_sleep(0.2)

    def _remove_target_device(
        self, index: int | None, phase: str
    ) -> BluetoothActionResult:
        self._ensure_not_stopped()
        prefix = "[前置条件]" if index is None else f"[第{index}轮]"
        self._log("INFO", f"{prefix} {phase}：开始删除已配对蓝牙设备。")
        return self._bluetooth.remove_target(
            self._settings.bt_name_keyword,
            self._settings.bt_mac,
            self._settings.bt_match_mode,
            timeout_sec=max(1.0, self._settings.state_timeout_ms / 1000.0),
            sample_interval_sec=max(0.1, self._settings.sample_interval_ms / 1000.0),
            log_cb=self._log,
        )

    def _pair_target_device(
        self, index: int | None, phase: str
    ) -> BluetoothActionResult:
        prefix = "[前置条件]" if index is None else f"[第{index}轮]"
        self._log("INFO", f"{prefix} {phase}：开始添加并连接蓝牙设备。")
        self._trigger_pairing_mode(index)
        return self._bluetooth.pair_target(
            self._settings.bt_name_keyword,
            self._settings.bt_mac,
            self._settings.bt_match_mode,
            timeout_sec=max(1.0, self._settings.state_timeout_ms / 1000.0),
            sample_interval_sec=max(0.1, self._settings.sample_interval_ms / 1000.0),
            log_cb=self._log,
        )

    def _wait_for_target_connected(
        self, phase: str
    ) -> tuple[bool, list[object]]:
        deadline = time.monotonic() + max(1.0, self._settings.state_timeout_ms / 1000.0)
        interval = max(0.1, self._settings.sample_interval_ms / 1000.0)
        last_matched: list[object] = []
        while time.monotonic() <= deadline:
            self._ensure_not_stopped()
            connected, matched = self._bluetooth.is_target_connected(
                self._settings.bt_name_keyword,
                self._settings.bt_mac,
                self._settings.bt_match_mode,
            )
            last_matched = list(matched)
            if connected:
                self._log("INFO", f"{phase}：目标蓝牙设备已连接。")
                return True, last_matched
            self._controlled_sleep(interval)
        self._log("WARNING", f"{phase}：等待目标蓝牙设备连接超时。")
        return False, last_matched

    def _check_target_connected_once(
        self, phase: str
    ) -> tuple[bool, list[object]]:
        self._ensure_not_stopped()
        connected, matched = self._bluetooth.is_target_connected(
            self._settings.bt_name_keyword,
            self._settings.bt_mac,
            self._settings.bt_match_mode,
        )
        matched_list = list(matched)
        if connected:
            self._log("INFO", f"{phase}：目标蓝牙设备已连接。")
        else:
            self._log("INFO", f"{phase}：当前未检测到目标蓝牙设备已连接。")
        return connected, matched_list

    def _set_channel_state(self, channel: int, on: bool, phase: str) -> None:
        action = "打开" if on else "关闭"
        self._ensure_not_stopped()
        try:
            self._relay.set_channel_state(channel, on)
            self._log("INFO", f"{phase}: 通道{channel}{action}命令已发送。")
        except Exception as exc:
            raise NonRecoverableError(f"继电器通道{channel}{action}失败: {exc}") from exc

    def _controlled_sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._ensure_not_stopped()
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def _ensure_not_stopped(self) -> None:
        if self._stop_flag.is_set():
            raise StopRequested

    def _log(self, level: str, message: str) -> None:
        if self._log_cb:
            self._log_cb(level, message)
            return
        log_level = getattr(logging, level.upper(), logging.INFO)
        _LOGGER.log(log_level, message)

    def _emit_progress(self, done: int, total: int) -> None:
        if self._progress_cb:
            self._progress_cb(done, total)

    def _emit_cycle(self, result: BluetoothConnectCycleResult) -> None:
        if self._cycle_cb:
            self._cycle_cb(result)
