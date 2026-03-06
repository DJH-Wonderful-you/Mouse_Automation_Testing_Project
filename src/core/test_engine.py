from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from PySide6.QtCore import QObject, Signal, Slot

from src.core.types import AppSettings, CycleResult, RunSummary, VerificationPolicy

_LOGGER = logging.getLogger("test_engine")


class RelayController(Protocol):
    def query_channel_state(self, channel: int) -> bool: ...

    def set_channel_state(self, channel: int, on: bool) -> None: ...


class MultimeterReader(Protocol):
    def read_voltage(self, attempts: int = 1) -> float | None: ...


class BluetoothChecker(Protocol):
    def is_target_connected(
        self, name_keyword: str, mac: str, mode: str
    ) -> tuple[bool, list[object]]: ...


@dataclass(slots=True)
class _WaitResult:
    ok: bool
    reason: str
    voltage: float | None
    bt_connected: bool | None


class StopRequested(Exception):
    pass


class NonRecoverableError(Exception):
    pass


class PowerCycleRunner:
    def __init__(
        self,
        relay: RelayController,
        multimeter: MultimeterReader,
        bluetooth: BluetoothChecker,
        settings: AppSettings,
        policy: VerificationPolicy,
        log_cb: Callable[[str, str], None] | None = None,
        progress_cb: Callable[[int, int], None] | None = None,
        cycle_cb: Callable[[CycleResult], None] | None = None,
    ) -> None:
        self._relay = relay
        self._multimeter = multimeter
        self._bluetooth = bluetooth
        self._settings = settings
        self._policy = policy
        self._log_cb = log_cb
        self._progress_cb = progress_cb
        self._cycle_cb = cycle_cb
        self._stop_flag = threading.Event()

    def stop(self) -> None:
        self._stop_flag.set()

    def run(self) -> RunSummary:
        success_count = 0
        fail_count = 0
        total = max(0, self._settings.test_count)

        self._log("INFO", f"测试开始，总轮次: {total}")
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
                result = CycleResult(
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
            success_count=success_count, fail_count=fail_count, success_rate=success_rate
        )
        self._log(
            "INFO",
            f"测试结束：成功 {success_count}，失败 {fail_count}，成功率 {success_rate:.2f}%",
        )
        return summary

    def _run_single_cycle(self, index: int) -> CycleResult:
        channel = self._settings.relay_channel
        threshold = self._settings.voltage_threshold_v

        try:
            current_state = self._relay.query_channel_state(channel)
        except Exception as exc:
            raise NonRecoverableError(f"读取继电器状态失败: {exc}") from exc

        self._log("INFO", f"[第{index}轮] 当前继电器通道{channel}状态: {'开' if current_state else '关'}")

        if current_state:
            self._set_power(channel, False, "执行断电准备")
            self._controlled_sleep(0.1)

        off_result = self._wait_for_expected_state(expected_power_on=False, threshold=threshold)
        if not off_result.ok:
            return CycleResult(
                index=index,
                success=False,
                reason=f"断电验证失败: {off_result.reason}",
                voltage_off=off_result.voltage,
                bt_off_connected=off_result.bt_connected,
            )

        self._controlled_sleep(self._settings.interval_ms / 1000.0)
        self._set_power(channel, True, "执行上电")
        on_result = self._wait_for_expected_state(expected_power_on=True, threshold=threshold)
        if not on_result.ok:
            return CycleResult(
                index=index,
                success=False,
                reason=f"上电验证失败: {on_result.reason}",
                voltage_off=off_result.voltage,
                bt_off_connected=off_result.bt_connected,
                voltage_on=on_result.voltage,
                bt_on_connected=on_result.bt_connected,
            )

        return CycleResult(
            index=index,
            success=True,
            reason="断电/上电验证均通过",
            voltage_off=off_result.voltage,
            bt_off_connected=off_result.bt_connected,
            voltage_on=on_result.voltage,
            bt_on_connected=on_result.bt_connected,
        )

    def _wait_for_expected_state(
        self, expected_power_on: bool, threshold: float
    ) -> _WaitResult:
        timeout_s = max(0.1, self._policy.state_timeout_ms / 1000.0)
        interval_s = max(0.05, self._policy.sample_interval_ms / 1000.0)
        deadline = time.time() + timeout_s
        consecutive = 0
        last_voltage: float | None = None
        last_bt: bool | None = None

        while time.time() <= deadline:
            self._ensure_not_stopped()
            voltage = self._safe_read_voltage()
            bt_connected = self._safe_read_bluetooth_connected()
            last_voltage = voltage
            last_bt = bt_connected

            if voltage is None or bt_connected is None:
                consecutive = 0
            else:
                voltage_ok = voltage > threshold if expected_power_on else voltage < threshold
                bt_ok = bt_connected if expected_power_on else not bt_connected
                if voltage_ok and bt_ok:
                    consecutive += 1
                else:
                    consecutive = 0

                if consecutive >= max(1, self._policy.consecutive_pass_needed):
                    status_text = "上电" if expected_power_on else "断电"
                    self._log(
                        "INFO",
                        f"{status_text}状态确认通过: 电压={voltage:.3f}V, 蓝牙={'已连接' if bt_connected else '未连接'}",
                    )
                    return _WaitResult(True, "", voltage, bt_connected)

            self._controlled_sleep(interval_s)

        status_text = "上电" if expected_power_on else "断电"
        reason = (
            f"{status_text}状态等待超时(last: voltage={last_voltage}, bt_connected={last_bt})"
        )
        return _WaitResult(False, reason, last_voltage, last_bt)

    def _safe_read_voltage(self) -> float | None:
        try:
            voltage = self._multimeter.read_voltage(attempts=1)
            if voltage is not None:
                self._log("DEBUG", f"采样电压: {voltage:.4f}V")
            return voltage
        except Exception as exc:  # noqa: BLE001
            self._log("WARNING", f"读取电压失败: {exc}")
            return None

    def _safe_read_bluetooth_connected(self) -> bool | None:
        try:
            connected, _ = self._bluetooth.is_target_connected(
                self._settings.bt_name_keyword,
                self._settings.bt_mac,
                self._settings.bt_match_mode,
            )
            self._log("DEBUG", f"蓝牙连接状态: {'已连接' if connected else '未连接'}")
            return connected
        except Exception as exc:  # noqa: BLE001
            self._log("WARNING", f"读取蓝牙状态失败: {exc}")
            return None

    def _set_power(self, channel: int, on: bool, phase: str) -> None:
        action = "上电" if on else "断电"
        self._ensure_not_stopped()
        try:
            self._relay.set_channel_state(channel, on)
            self._log("INFO", f"{phase}: 通道{channel}{action}命令已发送。")
        except Exception as exc:
            raise NonRecoverableError(f"继电器{action}失败: {exc}") from exc

    def _controlled_sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        start = time.time()
        while time.time() - start < seconds:
            self._ensure_not_stopped()
            time.sleep(min(0.05, seconds))

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

    def _emit_cycle(self, result: CycleResult) -> None:
        if self._cycle_cb:
            self._cycle_cb(result)


class TestEngineWorker(QObject):
    sig_log = Signal(str, str)
    sig_progress = Signal(int, int)
    sig_cycle_result = Signal(object)
    sig_finished = Signal(int, int, float)
    sig_error = Signal(str)

    def __init__(self, runner: PowerCycleRunner) -> None:
        super().__init__()
        self._runner = runner

    @Slot()
    def run(self) -> None:
        try:
            summary = self._runner.run()
            self.sig_finished.emit(
                summary.success_count, summary.fail_count, summary.success_rate
            )
        except Exception as exc:  # noqa: BLE001
            self.sig_error.emit(str(exc))

    def stop(self) -> None:
        self._runner.stop()
