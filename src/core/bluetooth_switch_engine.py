from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Protocol

from src.core.bluetooth_adapter_control import (
    get_all_enumerators,
    check_device_valid,
    switch_device,
)
from src.core.bluetooth_probe import is_target_connected, BluetoothDeviceInfo
from src.core.types import (
    BluetoothConnectCycleResult,
    BluetoothSwitchSettings,
    RunSummary,
    BtMatchMode,
)

_LOGGER = logging.getLogger("bluetooth.switch_engine")


class BluetoothSwitchRunner:
    def __init__(
        self,
        settings: BluetoothSwitchSettings,
        log_cb: Callable[[str, str], None] | None = None,
        progress_cb: Callable[[int, int], None] | None = None,
        cycle_cb: Callable[[BluetoothConnectCycleResult], None] | None = None,
    ) -> None:
        self._settings = settings
        self._log_cb = log_cb
        self._progress_cb = progress_cb
        self._cycle_cb = cycle_cb
        self._stop_flag = threading.Event()
        self._valid_devices = []

    def stop(self) -> None:
        self._stop_flag.set()

    def run(self) -> RunSummary:
        success_count = 0
        fail_count = 0
        total = max(0, self._settings.test_count)

        self._log("INFO", f"蓝牙开关测试开始，总轮次: {total}")

        # 初始化：获取并验证蓝牙枚举器设备
        if not self._init_bluetooth_devices():
            self._log("ERROR", "初始化蓝牙设备失败，测试无法开始")
            return RunSummary(success_count=0, fail_count=0, success_rate=0.0)

        for index in range(1, total + 1):
            if self._stop_flag.is_set():
                self._log("WARNING", "检测到停止请求，测试提前结束。")
                break

            try:
                result = self._run_single_cycle(index)
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
            f"蓝牙开关测试结束：成功 {success_count}，失败 {fail_count}，成功率 {success_rate:.2f}%",
        )
        return summary

    def _init_bluetooth_devices(self) -> bool:
        """初始化蓝牙设备"""
        self._log("INFO", "正在检测可操作的蓝牙枚举器设备...")
        all_devices = get_all_enumerators()
        if not all_devices:
            self._log("ERROR", "未找到蓝牙枚举器设备")
            return False

        self._valid_devices = []
        for name, iid in all_devices:
            if check_device_valid(name, iid):
                self._valid_devices.append((name, iid))

        if not self._valid_devices:
            self._log("ERROR", "未找到可操作的有效蓝牙枚举器")
            return False

        self._log("INFO", f"已找到 {len(self._valid_devices)} 个可操作的蓝牙枚举器")
        for name, iid in self._valid_devices:
            self._log("INFO", f"  - {name} | {iid}")

        return True

    def _run_single_cycle(self, index: int) -> BluetoothConnectCycleResult:
        """执行单轮蓝牙开关测试"""
        self._log("INFO", f"[第{index}轮] 开始蓝牙开关测试")

        # 获取匹配模式
        bt_match_mode: BtMatchMode = self._settings.bt_match_mode if self._settings.bt_match_mode else "name_or_mac"
        name_keyword = self._settings.bt_name_keyword
        mac = self._settings.bt_mac

        # 1. 检查初始连接状态
        initial_connected, initial_devices = is_target_connected(name_keyword, mac, bt_match_mode)
        status_text = "已连接" if initial_connected else "未连接"
        self._log("INFO", f"[第{index}轮] 初始连接状态: {status_text}")

        # 输出匹配到的设备详细信息
        if initial_devices:
            self._log("INFO", f"[第{index}轮] 匹配到 {len(initial_devices)} 个设备：")
            for device in initial_devices:
                connected_hint = getattr(device, "connected", None)
                connected_text = "未知" if connected_hint is None else ("已连接" if connected_hint else "未连接")
                self._log("INFO", f"[第{index}轮]   - 实例ID={device.instance_id} | 连接状态={connected_text}")

        # 2. 禁用蓝牙设备
        self._log("INFO", f"[第{index}轮] 开始禁用蓝牙设备...")
        disable_success = True
        for name, iid in self._valid_devices:
            if not switch_device(name, iid, enable=False):
                disable_success = False
                break

        if not disable_success:
            return BluetoothConnectCycleResult(
                index=index,
                success=False,
                reason="禁用蓝牙设备失败",
            )

        # 等待一段时间，让蓝牙设备完全禁用
        wait_seconds = self._settings.state_timeout_ms / 1000.0
        self._log("INFO", f"[第{index}轮] 等待 {wait_seconds:.2f} 秒让蓝牙设备完全禁用...")
        time.sleep(wait_seconds)

        # 3. 检查禁用后的连接状态
        disabled_connected, disabled_devices = is_target_connected(name_keyword, mac, bt_match_mode)
        disabled_status_text = "已连接" if disabled_connected else "未连接"
        self._log("INFO", f"[第{index}轮] 禁用后连接状态: {disabled_status_text}")

        # 输出禁用后匹配到的设备详细信息
        if disabled_devices:
            self._log("INFO", f"[第{index}轮] 禁用后匹配到 {len(disabled_devices)} 个设备：")
            for device in disabled_devices:
                connected_hint = getattr(device, "connected", None)
                connected_text = "未知" if connected_hint is None else ("已连接" if connected_hint else "未连接")
                self._log("INFO", f"[第{index}轮]   - 实例ID={device.instance_id} | 连接状态={connected_text}")

        if disabled_connected:
            self._log("WARNING", f"[第{index}轮] 禁用后设备仍连接，可能异常")

        # 4. 启用蓝牙设备
        self._log("INFO", f"[第{index}轮] 开始启用蓝牙设备...")
        enable_success = True
        for name, iid in self._valid_devices:
            if not switch_device(name, iid, enable=True):
                enable_success = False
                break

        if not enable_success:
            return BluetoothConnectCycleResult(
                index=index,
                success=False,
                reason="启用蓝牙设备失败",
            )

        # 等待一段时间，让蓝牙设备完全启用并重新连接
        self._log("INFO", f"[第{index}轮] 等待 {wait_seconds:.2f} 秒让蓝牙设备完全启用...")
        time.sleep(wait_seconds)

        # 5. 检查启用后的连接状态
        enabled_connected, enabled_devices = is_target_connected(name_keyword, mac, bt_match_mode)
        enabled_status_text = "已连接" if enabled_connected else "未连接"
        self._log("INFO", f"[第{index}轮] 启用后连接状态: {enabled_status_text}")

        # 输出启用后匹配到的设备详细信息
        if enabled_devices:
            self._log("INFO", f"[第{index}轮] 启用后匹配到 {len(enabled_devices)} 个设备：")
            for device in enabled_devices:
                connected_hint = getattr(device, "connected", None)
                connected_text = "未知" if connected_hint is None else ("已连接" if connected_hint else "未连接")
                self._log("INFO", f"[第{index}轮]   - 实例ID={device.instance_id} | 连接状态={connected_text}")

        # 6. 判断测试结果
        if not enable_success or not disable_success:
            return BluetoothConnectCycleResult(
                index=index,
                success=False,
                reason="蓝牙设备开关操作失败",
            )

        if not enabled_connected:
            return BluetoothConnectCycleResult(
                index=index,
                success=False,
                reason="启用后设备未连接",
            )

        return BluetoothConnectCycleResult(
            index=index,
            success=True,
            reason="蓝牙开关测试通过",
        )

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
