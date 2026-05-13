from __future__ import annotations

import logging
import platform
import subprocess
import threading
import time
from typing import Callable, Protocol

from src.core.types import BluetoothConnectCycleResult, BluetoothSwitchSettings, RunSummary

_LOGGER = logging.getLogger("bluetooth.ui_switch_runner")

VK_TAB = 0x09
VK_SPACE = 0x20
VK_ALT = 0x12
VK_F4 = 0x73


class BluetoothChecker(Protocol):
    def is_target_connected(
        self, name_keyword: str, mac: str, mode: str
    ) -> tuple[bool, list[object]]: ...


class BluetoothUiSwitchRunner:
    def __init__(
        self,
        bluetooth: BluetoothChecker,
        settings: BluetoothSwitchSettings,
        log_cb: Callable[[str, str], None] | None = None,
        progress_cb: Callable[[int, int], None] | None = None,
        cycle_cb: Callable[[BluetoothConnectCycleResult], None] | None = None,
    ) -> None:
        self._bluetooth = bluetooth
        self._settings = settings
        self._log_cb = log_cb
        self._progress_cb = progress_cb
        self._cycle_cb = cycle_cb
        self._stop_flag = threading.Event()

    def stop(self) -> None:
        self._stop_flag.set()

    def run(self) -> RunSummary:
        total = max(0, self._settings.test_count)
        success_count = 0
        fail_count = 0
        self._log("INFO", f"蓝牙 UI 开关测试开始，总轮次: {total}")

        try:
            tab_count = self._resolve_tab_count()
            previous_connected = self._read_connected_state("初始")
            self._initialize_settings_window()

            for index in range(1, total + 1):
                if self._stop_flag.is_set():
                    self._log("WARNING", "检测到停止请求，测试提前结束。")
                    break

                result = self._run_single_cycle(index, tab_count, previous_connected)
                if result.success:
                    success_count += 1
                    previous_connected = not previous_connected
                else:
                    fail_count += 1
                self._emit_cycle(result)
                self._emit_progress(success_count + fail_count, total)
        except Exception as exc:  # noqa: BLE001
            self._log("ERROR", f"蓝牙 UI 开关测试异常: {exc}")
            if not self._stop_flag.is_set():
                fail_count += 1

        done = success_count + fail_count
        success_rate = (success_count / done * 100.0) if done else 0.0
        self._log(
            "INFO",
            f"蓝牙 UI 开关测试结束：成功 {success_count}，失败 {fail_count}，成功率 {success_rate:.2f}%",
        )
        return RunSummary(success_count, fail_count, success_rate)

    def _resolve_tab_count(self) -> int:
        version = platform.release()
        parts = platform.version().split(".")
        build = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        if version == "10" and build >= 22000:
            self._log("INFO", "检测到 Windows 11，TAB 次数使用 3。")
            return 3
        self._log("INFO", "检测到 Windows 10/未知版本，TAB 次数使用 1。")
        return 1

    def _initialize_settings_window(self) -> None:
        self._log("INFO", "初始化蓝牙设置窗口。")
        self._open_bluetooth_settings()
        self._controlled_sleep(3.0)
        self._press_combination_key(VK_ALT, VK_F4)
        self._controlled_sleep(1.0)

    def _run_single_cycle(
        self, index: int, tab_count: int, previous_connected: bool
    ) -> BluetoothConnectCycleResult:
        self._log("INFO", f"[第{index}轮] 开始蓝牙 UI 开关测试。")
        try:
            self._open_bluetooth_settings()
            self._controlled_sleep(2.0)
            for _ in range(tab_count):
                self._press_key(VK_TAB)
                self._controlled_sleep(1.0)
            self._press_key(VK_SPACE)
            self._controlled_sleep(max(1.0, self._settings.state_timeout_ms / 1000.0))
            connected = self._read_connected_state(f"第{index}轮")
            success = connected != previous_connected
            reason = "蓝牙连接状态已变化" if success else "蓝牙连接状态未变化"
            return BluetoothConnectCycleResult(
                index=index,
                success=success,
                reason=reason,
            )
        finally:
            self._press_combination_key(VK_ALT, VK_F4)
            self._controlled_sleep(1.0)

    def _read_connected_state(self, phase: str) -> bool:
        connected, matched = self._bluetooth.is_target_connected(
            self._settings.bt_name_keyword,
            self._settings.bt_mac,
            self._settings.bt_match_mode,
        )
        status_text = "已连接" if connected else "未连接"
        self._log("INFO", f"{phase}蓝牙连接状态: {status_text}，匹配设备 {len(matched)} 个。")
        return connected

    def _open_bluetooth_settings(self) -> None:
        subprocess.run(
            "control bthprops.cpl",
            shell=True,
            check=False,
            startupinfo=self._startupinfo(),
        )

    def _startupinfo(self):
        if not hasattr(subprocess, "STARTUPINFO"):
            return None
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        return startupinfo

    def _press_key(self, vk_code: int) -> None:
        from ctypes import windll

        windll.user32.keybd_event(vk_code, 0, 0, 0)
        windll.user32.keybd_event(vk_code, 0, 2, 0)

    def _press_combination_key(self, vk_code1: int, vk_code2: int) -> None:
        from ctypes import windll

        windll.user32.keybd_event(vk_code1, 0, 0, 0)
        windll.user32.keybd_event(vk_code2, 0, 0, 0)
        windll.user32.keybd_event(vk_code2, 0, 2, 0)
        windll.user32.keybd_event(vk_code1, 0, 2, 0)

    def _controlled_sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            if self._stop_flag.is_set():
                raise RuntimeError("测试已停止")
            time.sleep(min(0.05, deadline - time.monotonic()))

    def _log(self, level: str, message: str) -> None:
        if self._log_cb:
            self._log_cb(level, message)
            return
        _LOGGER.log(getattr(logging, level.upper(), logging.INFO), message)

    def _emit_progress(self, done: int, total: int) -> None:
        if self._progress_cb:
            self._progress_cb(done, total)

    def _emit_cycle(self, result: BluetoothConnectCycleResult) -> None:
        if self._cycle_cb:
            self._cycle_cb(result)
