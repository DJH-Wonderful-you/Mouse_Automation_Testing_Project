from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from src.core.bluetooth_probe import (
    BluetoothDeviceInfo,
    BluetoothProbe,
    _build_hidden_subprocess_kwargs,
    _decode_process_bytes,
    match_target,
    normalize_mac,
)
from src.core.types import BtMatchMode

_LOGGER = logging.getLogger("bluetooth.pairing")

LogCallback = Callable[[str, str], None] | None


@dataclass(slots=True)
class BluetoothActionResult:
    ok: bool
    reason: str = ""
    matched: list[BluetoothDeviceInfo] | None = None


class BluetoothManager(Protocol):
    def query_paired_devices(
        self, name_keyword: str, mac: str, mode: BtMatchMode
    ) -> list[BluetoothDeviceInfo]: ...

    def is_target_connected(
        self, name_keyword: str, mac: str, mode: BtMatchMode
    ) -> tuple[bool, list[BluetoothDeviceInfo]]: ...

    def pair_target(
        self,
        name_keyword: str,
        mac: str,
        mode: BtMatchMode,
        *,
        timeout_sec: float,
        sample_interval_sec: float,
        log_cb: LogCallback = None,
    ) -> BluetoothActionResult: ...

    def remove_target(
        self,
        name_keyword: str,
        mac: str,
        mode: BtMatchMode,
        *,
        timeout_sec: float,
        sample_interval_sec: float,
        log_cb: LogCallback = None,
    ) -> BluetoothActionResult: ...


class SystemBluetoothManager:
    def __init__(self, probe: BluetoothProbe | None = None) -> None:
        self._probe = probe or BluetoothProbe(
            inventory_cache_ttl_sec=0.0,
            target_cache_ttl_sec=0.0,
        )

    def query_paired_devices(
        self, name_keyword: str, mac: str, mode: BtMatchMode
    ) -> list[BluetoothDeviceInfo]:
        devices = self._probe.query_devices()
        return [
            device
            for device in devices
            if match_target(device, name_keyword=name_keyword, mac=mac, mode=mode)
        ]

    def is_target_connected(
        self, name_keyword: str, mac: str, mode: BtMatchMode
    ) -> tuple[bool, list[BluetoothDeviceInfo]]:
        return self._probe.is_target_connected(name_keyword, mac, mode)

    def pair_target(
        self,
        name_keyword: str,
        mac: str,
        mode: BtMatchMode,
        *,
        timeout_sec: float,
        sample_interval_sec: float,
        log_cb: LogCallback = None,
    ) -> BluetoothActionResult:
        normalized_name = (name_keyword or "").strip()
        normalized_mac = normalize_mac(mac)
        if not normalized_name:
            return BluetoothActionResult(
                ok=False,
                reason="自动配对需要填写蓝牙名称关键字，以便在 Windows 配对列表中定位目标设备。",
            )

        _log(log_cb, "INFO", "开始调用系统蓝牙设置页执行配对。")
        ui_result = _pair_via_settings_ui(
            normalized_name,
            normalized_mac,
            timeout_sec=max(5.0, timeout_sec),
        )
        if not ui_result.ok:
            return ui_result

        deadline = time.monotonic() + max(1.0, timeout_sec)
        interval = max(0.1, sample_interval_sec)
        last_matched: list[BluetoothDeviceInfo] = []
        while time.monotonic() <= deadline:
            connected, matched = self.is_target_connected(
                normalized_name,
                normalized_mac,
                mode,
            )
            last_matched = matched
            if connected:
                _log(log_cb, "INFO", "蓝牙配对并连接成功。")
                return BluetoothActionResult(ok=True, matched=matched)
            time.sleep(interval)

        paired = self.query_paired_devices(normalized_name, normalized_mac, mode)
        if paired:
            return BluetoothActionResult(
                ok=False,
                reason="设备已进入已配对列表，但在超时时间内未达到已连接状态。",
                matched=paired or last_matched,
            )
        return BluetoothActionResult(
            ok=False,
            reason="配对界面操作已完成，但系统中仍未检测到目标设备。",
            matched=last_matched,
        )

    def remove_target(
        self,
        name_keyword: str,
        mac: str,
        mode: BtMatchMode,
        *,
        timeout_sec: float,
        sample_interval_sec: float,
        log_cb: LogCallback = None,
    ) -> BluetoothActionResult:
        normalized_name = (name_keyword or "").strip()
        normalized_mac = normalize_mac(mac)
        matched = self.query_paired_devices(normalized_name, normalized_mac, mode)
        if not matched:
            return BluetoothActionResult(ok=True, reason="目标设备当前未处于已配对状态。")

        interval = max(0.1, sample_interval_sec)
        errors: list[str] = []

        if normalized_name:
            _log(log_cb, "INFO", "开始调用系统蓝牙设置页执行删除配对。")
            ui_result = _remove_via_settings_ui(
                normalized_name,
                normalized_mac,
                timeout_sec=max(5.0, timeout_sec),
            )
            if ui_result.ok:
                if ui_result.reason:
                    _log(log_cb, "INFO", ui_result.reason)
                deadline = time.monotonic() + max(1.0, timeout_sec)
                while time.monotonic() <= deadline:
                    remaining = self.query_paired_devices(normalized_name, normalized_mac, mode)
                    if not remaining:
                        _log(log_cb, "INFO", "目标设备已从已配对列表中移除。")
                        return BluetoothActionResult(ok=True, matched=matched)
                    time.sleep(interval)
                errors.append("设置页删除配对已执行，但设备仍保留在已配对列表中。")
            else:
                errors.append(f"设置页删除配对失败: {ui_result.reason}")
        else:
            _log(
                log_cb,
                "WARNING",
                "未填写蓝牙名称关键字，跳过设置页删除配对，回退到设备节点删除。",
            )

        instance_ids = [device.instance_id for device in matched if device.instance_id]
        if not instance_ids:
            reason = "已匹配到目标设备，但缺少可用于删除配对的实例 ID。"
            if errors:
                reason = f"删除已配对设备失败: {'; '.join(errors)}；{reason}"
            return BluetoothActionResult(ok=False, reason=reason, matched=matched)

        for instance_id in dict.fromkeys(instance_ids):
            _log(log_cb, "INFO", f"尝试删除已配对设备节点: {instance_id}")
            error = _remove_device_instance(instance_id)
            if error:
                if _is_access_denied_error(error):
                    error = f"{error}（当前路径通常需要管理员权限）"
                errors.append(f"{instance_id}: {error}")

        deadline = time.monotonic() + max(1.0, timeout_sec)
        while time.monotonic() <= deadline:
            remaining = self.query_paired_devices(normalized_name, normalized_mac, mode)
            if not remaining:
                _log(log_cb, "INFO", "目标设备已从已配对列表中移除。")
                return BluetoothActionResult(ok=True, matched=matched)
            time.sleep(interval)

        reason = "等待设备从已配对列表移除超时。"
        if errors:
            reason = f"删除已配对设备失败: {'; '.join(errors)}"
        return BluetoothActionResult(ok=False, reason=reason, matched=matched)


class SimulatedBluetoothManager:
    def __init__(
        self,
        *,
        device_name: str = "SimMouse",
        device_mac: str = "00:11:22:AA:BB:CC",
    ) -> None:
        self._device_name = device_name
        self._device_mac = normalize_mac(device_mac) or "00:11:22:AA:BB:CC"
        self._paired = False
        self._connected = False

    def query_paired_devices(
        self, name_keyword: str, mac: str, mode: BtMatchMode
    ) -> list[BluetoothDeviceInfo]:
        if not self._paired:
            return []
        device = self._build_device()
        if match_target(device, name_keyword=name_keyword, mac=mac, mode=mode):
            return [device]
        return []

    def is_target_connected(
        self, name_keyword: str, mac: str, mode: BtMatchMode
    ) -> tuple[bool, list[BluetoothDeviceInfo]]:
        matched = self.query_paired_devices(name_keyword, mac, mode)
        return self._connected and bool(matched), matched

    def pair_target(
        self,
        name_keyword: str,
        mac: str,
        mode: BtMatchMode,
        *,
        timeout_sec: float,
        sample_interval_sec: float,
        log_cb: LogCallback = None,
    ) -> BluetoothActionResult:
        _ = timeout_sec, sample_interval_sec
        self._paired = True
        self._connected = True
        _log(log_cb, "INFO", "仿真蓝牙配对成功。")
        return BluetoothActionResult(ok=True, matched=self.query_paired_devices(name_keyword, mac, mode))

    def remove_target(
        self,
        name_keyword: str,
        mac: str,
        mode: BtMatchMode,
        *,
        timeout_sec: float,
        sample_interval_sec: float,
        log_cb: LogCallback = None,
    ) -> BluetoothActionResult:
        _ = timeout_sec, sample_interval_sec
        matched = self.query_paired_devices(name_keyword, mac, mode)
        self._paired = False
        self._connected = False
        _log(log_cb, "INFO", "仿真蓝牙配对记录已删除。")
        return BluetoothActionResult(ok=True, matched=matched)

    def seed_paired_device(self, *, connected: bool = True) -> None:
        self._paired = True
        self._connected = connected

    def _build_device(self) -> BluetoothDeviceInfo:
        return BluetoothDeviceInfo(
            name=self._device_name,
            instance_id="SIM\\BTH\\001122AABBCC",
            status="OK" if self._connected else "Disconnected",
            class_name="Bluetooth",
            present=self._connected,
            mac=self._device_mac,
            connected=self._connected,
        )


def _remove_device_instance(instance_id: str) -> str:
    try:
        completed = subprocess.run(
            ["pnputil", "/remove-device", instance_id],
            capture_output=True,
            text=False,
            timeout=15,
            check=False,
            **_build_hidden_subprocess_kwargs(),
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("删除蓝牙设备节点失败 %s: %s", instance_id, exc)
        return str(exc)

    if completed.returncode == 0:
        return ""

    stderr = _decode_process_bytes(completed.stderr).strip()
    stdout = _decode_process_bytes(completed.stdout).strip()
    return (stderr or stdout or f"pnputil 返回码 {completed.returncode}")[:300]

def _is_access_denied_error(message: str) -> bool:
    normalized = (message or "").strip().lower()
    return "access is denied" in normalized or "拒绝访问" in normalized


@dataclass(slots=True)
class _SettingsUiActionResult:
    ok: bool
    reason: str = ""
    selected_name: str = ""


@dataclass(slots=True)
class _UiScriptRunResult:
    stdout: str
    stderr: str
    returncode: int | None


def _pair_via_settings_ui(name_keyword: str, mac: str, *, timeout_sec: float) -> BluetoothActionResult:
    pywinauto_result = _pair_via_settings_ui_pywinauto(
        name_keyword,
        mac,
        timeout_sec=timeout_sec,
    )
    if pywinauto_result.ok:
        return _build_pair_ui_action_result(pywinauto_result)

    _LOGGER.warning("pywinauto 蓝牙配对失败，回退到 PowerShell UIA: %s", pywinauto_result.reason)
    fallback_result = _pair_via_settings_ui_powershell(
        name_keyword,
        mac,
        timeout_sec=timeout_sec,
    )
    if fallback_result.ok:
        return _build_pair_ui_action_result(fallback_result)

    return BluetoothActionResult(
        ok=False,
        reason=_merge_ui_action_failure_reasons(
            "蓝牙配对",
            pywinauto_result.reason,
            fallback_result.reason,
        ),
    )


def _remove_via_settings_ui(name_keyword: str, mac: str, *, timeout_sec: float) -> BluetoothActionResult:
    pywinauto_result = _remove_via_settings_ui_pywinauto(
        name_keyword,
        mac,
        timeout_sec=timeout_sec,
    )
    if pywinauto_result.ok:
        return _build_remove_ui_action_result(pywinauto_result)

    _LOGGER.warning("pywinauto 蓝牙删除配对失败，回退到 PowerShell UIA: %s", pywinauto_result.reason)
    fallback_result = _remove_via_settings_ui_powershell(
        name_keyword,
        mac,
        timeout_sec=timeout_sec,
    )
    if fallback_result.ok:
        return _build_remove_ui_action_result(fallback_result)

    return BluetoothActionResult(
        ok=False,
        reason=_merge_ui_action_failure_reasons(
            "蓝牙删除配对",
            pywinauto_result.reason,
            fallback_result.reason,
        ),
    )


def _build_pair_ui_action_result(result: _SettingsUiActionResult) -> BluetoothActionResult:
    if result.ok and result.selected_name:
        return BluetoothActionResult(ok=True, reason=f"已在系统配对界面选择设备: {result.selected_name}")
    if result.ok:
        return BluetoothActionResult(ok=True, reason="已完成系统配对界面操作。")
    return BluetoothActionResult(ok=False, reason=result.reason or "蓝牙配对界面操作失败。")


def _build_remove_ui_action_result(result: _SettingsUiActionResult) -> BluetoothActionResult:
    if result.ok and result.selected_name:
        return BluetoothActionResult(ok=True, reason=f"已在系统设置页删除设备: {result.selected_name}")
    if result.ok:
        return BluetoothActionResult(ok=True, reason="已完成系统删除配对界面操作。")
    return BluetoothActionResult(ok=False, reason=result.reason or "蓝牙删除配对界面操作失败。")


def _merge_ui_action_failure_reasons(action_label: str, primary_reason: str, fallback_reason: str) -> str:
    primary = (primary_reason or "").strip()
    fallback = (fallback_reason or "").strip()
    if primary and fallback and primary != fallback:
        return f"{action_label}界面自动化失败: pywinauto={primary[:300]}；PowerShell UIA={fallback[:300]}"
    if primary:
        return primary[:300]
    if fallback:
        return fallback[:300]
    return f"{action_label}界面自动化失败。"


def _pair_via_settings_ui_pywinauto(
    name_keyword: str,
    mac: str,
    *,
    timeout_sec: float,
) -> _SettingsUiActionResult:
    desktop_factory, send_keys, import_error = _load_pywinauto_backend()
    if desktop_factory is None or send_keys is None:
        return _SettingsUiActionResult(ok=False, reason=import_error or "pywinauto 不可用。")

    launch_error = _launch_bluetooth_settings()
    if launch_error:
        return _SettingsUiActionResult(ok=False, reason=f"无法打开系统蓝牙设置页: {launch_error[:300]}")

    try:
        desktop = desktop_factory(backend="uia")
        settings_window = _wait_for_window(
            desktop,
            ["Bluetooth", "蓝牙", "设置", "Settings"],
            timeout_sec=12.0,
        )
        if settings_window is None:
            return _SettingsUiActionResult(ok=False, reason="未找到系统蓝牙设置窗口。")

        add_button = _wait_for_element(
            [settings_window],
            ["Add device", "添加设备"],
            ["Button"],
            contains_match=False,
            timeout_sec=10.0,
        )
        if add_button is None:
            return _SettingsUiActionResult(ok=False, reason="未找到“添加设备”按钮。")
        if not _invoke_element(add_button, send_keys):
            return _SettingsUiActionResult(ok=False, reason="无法触发“添加设备”按钮。")

        dialog = _wait_for_window(desktop, ["Add a device", "添加设备"], timeout_sec=1.0)
        if dialog is None:
            dialog = settings_window

        bluetooth_button = _wait_for_element(
            [dialog, desktop],
            ["Bluetooth", "蓝牙"],
            ["Button", "ListItem", "Custom", "Text"],
            contains_match=True,
            timeout_sec=6.0,
        )
        if bluetooth_button is None:
            return _SettingsUiActionResult(ok=False, reason="未找到“蓝牙”配对入口。")
        if not _invoke_element(bluetooth_button, send_keys):
            return _SettingsUiActionResult(ok=False, reason="无法触发“蓝牙”配对入口。")

        target_terms = [name_keyword]
        compact_mac = _compact_mac(mac)
        if compact_mac:
            target_terms.append(compact_mac)

        device_item = _wait_for_element(
            [dialog, desktop],
            target_terms,
            ["ListItem", "Button", "Custom", "Text", "DataItem"],
            contains_match=True,
            timeout_sec=timeout_sec,
        )
        if device_item is None:
            return _SettingsUiActionResult(ok=False, reason="在“添加设备”界面中未找到目标蓝牙设备。")

        clickable = _resolve_invokable_element(
            device_item,
            ["Button", "ListItem", "Custom", "Hyperlink", "DataItem"],
        )
        selected_name = _safe_element_name(clickable) or _safe_element_name(device_item)
        if not _invoke_element(clickable, send_keys):
            return _SettingsUiActionResult(
                ok=False,
                reason="已找到目标设备，但无法触发配对。",
                selected_name=selected_name,
            )

        button_deadline = time.monotonic() + min(6.0, max(2.0, timeout_sec / 3.0))
        while time.monotonic() <= button_deadline:
            clicked = False
            for button_names in (
                ["Pair", "配对"],
                ["Connect", "连接"],
                ["Done", "完成"],
                ["Close", "关闭"],
            ):
                if _click_optional_button(
                    [dialog, desktop],
                    button_names,
                    ["Button"],
                    send_keys,
                ):
                    clicked = True
                    time.sleep(0.5)
            if not clicked:
                time.sleep(0.35)

        return _SettingsUiActionResult(ok=True, selected_name=selected_name)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("pywinauto 蓝牙配对执行失败: %s", exc)
        return _SettingsUiActionResult(ok=False, reason=str(exc)[:300])


def _remove_via_settings_ui_pywinauto(
    name_keyword: str,
    mac: str,
    *,
    timeout_sec: float,
) -> _SettingsUiActionResult:
    desktop_factory, send_keys, import_error = _load_pywinauto_backend()
    if desktop_factory is None or send_keys is None:
        return _SettingsUiActionResult(ok=False, reason=import_error or "pywinauto 不可用。")

    launch_error = _launch_bluetooth_settings()
    if launch_error:
        return _SettingsUiActionResult(ok=False, reason=f"无法打开系统蓝牙设置页: {launch_error[:300]}")

    try:
        desktop = desktop_factory(backend="uia")
        settings_window = _wait_for_window(
            desktop,
            ["Bluetooth", "蓝牙", "设置", "Settings"],
            timeout_sec=12.0,
        )
        if settings_window is None:
            return _SettingsUiActionResult(ok=False, reason="未找到系统蓝牙设置窗口。")

        target_terms = [name_keyword]
        compact_mac = _compact_mac(mac)
        if compact_mac:
            target_terms.append(compact_mac)

        device_item = _wait_for_element(
            [settings_window, desktop],
            target_terms,
            ["ListItem", "Button", "Custom", "Text", "DataItem"],
            contains_match=True,
            timeout_sec=timeout_sec,
        )
        if device_item is None:
            return _SettingsUiActionResult(ok=False, reason="在系统蓝牙设备列表中未找到目标设备。")

        clickable = _resolve_invokable_element(
            device_item,
            ["Button", "ListItem", "Custom", "Hyperlink", "DataItem"],
        )
        selected_name = _safe_element_name(clickable) or _safe_element_name(device_item)
        _invoke_element(clickable, send_keys)
        time.sleep(0.8)

        remove_button = _find_element_near_item(
            device_item,
            ["Remove device", "删除设备", "Remove", "删除"],
            ["Button", "Hyperlink", "MenuItem"],
        )
        if remove_button is None:
            remove_button = _wait_for_element(
                [settings_window, desktop],
                ["Remove device", "删除设备", "Remove", "删除"],
                ["Button", "Hyperlink", "MenuItem"],
                contains_match=False,
                timeout_sec=8.0,
            )
        if remove_button is None:
            return _SettingsUiActionResult(
                ok=False,
                reason="已定位到目标设备，但未找到“删除设备”按钮。",
                selected_name=selected_name,
            )

        if not _invoke_element(remove_button, send_keys):
            return _SettingsUiActionResult(
                ok=False,
                reason="已找到“删除设备”按钮，但无法触发。",
                selected_name=selected_name,
            )

        time.sleep(0.5)
        confirm_button = _wait_for_element(
            [settings_window, desktop],
            ["Yes", "是", "Remove", "删除", "Unpair", "取消配对"],
            ["Button"],
            contains_match=False,
            timeout_sec=8.0,
        )
        if confirm_button is not None:
            _invoke_element(confirm_button, send_keys)

        return _SettingsUiActionResult(ok=True, selected_name=selected_name)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("pywinauto 蓝牙删除配对执行失败: %s", exc)
        return _SettingsUiActionResult(ok=False, reason=str(exc)[:300])


def _load_pywinauto_backend() -> tuple[object | None, object | None, str]:
    try:
        from pywinauto import Desktop
        from pywinauto.keyboard import send_keys
    except Exception as exc:  # noqa: BLE001
        return None, None, f"pywinauto 不可用: {exc}"
    return Desktop, send_keys, ""


def _launch_bluetooth_settings() -> str:
    try:
        subprocess.Popen(
            ["explorer.exe", "ms-settings:bluetooth"],
            **_build_hidden_subprocess_kwargs(),
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("打开系统蓝牙设置页失败: %s", exc)
        return str(exc)
    return ""


def _compact_mac(mac: str) -> str:
    return normalize_mac(mac).replace(":", "")


def _wait_for_window(desktop: object, terms: list[str], *, timeout_sec: float) -> object | None:
    deadline = time.monotonic() + max(0.1, timeout_sec)
    while time.monotonic() <= deadline:
        for window in _safe_windows(desktop):
            if _contains_any_term(_safe_element_name(window), terms):
                return window
        time.sleep(0.3)
    return None


def _wait_for_element(
    scopes: list[object],
    names: list[str],
    control_types: list[str],
    *,
    contains_match: bool,
    timeout_sec: float,
) -> object | None:
    deadline = time.monotonic() + max(0.1, timeout_sec)
    while time.monotonic() <= deadline:
        for scope in scopes:
            item = _find_element(scope, names, control_types, contains_match=contains_match)
            if item is not None:
                return item
        time.sleep(0.3)
    return None


def _click_optional_button(
    scopes: list[object],
    names: list[str],
    control_types: list[str],
    send_keys: object,
) -> bool:
    for scope in scopes:
        button = _find_element(scope, names, control_types, contains_match=False)
        if button is not None and _invoke_element(button, send_keys):
            return True
    return False


def _find_element(
    scope: object,
    names: list[str],
    control_types: list[str],
    *,
    contains_match: bool,
) -> object | None:
    normalized_types = {item.strip() for item in control_types if item.strip()}
    for item in _iter_scope_items(scope):
        control_type = _safe_control_type(item)
        if normalized_types and control_type not in normalized_types:
            continue
        if _match_element_name(_safe_element_name(item), names, contains_match=contains_match):
            return item
    return None


def _find_element_near_item(item: object, names: list[str], control_types: list[str]) -> object | None:
    current = item
    for _ in range(6):
        candidate = _find_element(current, names, control_types, contains_match=False)
        if candidate is not None:
            return candidate
        current = _safe_parent(current)
        if current is None:
            break
    return None


def _resolve_invokable_element(item: object, control_types: list[str]) -> object:
    current = item
    normalized_types = {control_type.strip() for control_type in control_types if control_type.strip()}
    for _ in range(6):
        if current is None:
            break
        if _safe_control_type(current) in normalized_types:
            return current
        current = _safe_parent(current)
    return item


def _invoke_element(item: object, send_keys: object) -> bool:
    if item is None:
        return False

    for method_name in ("invoke", "select"):
        method = getattr(item, method_name, None)
        if callable(method):
            try:
                method()
                return True
            except Exception:  # noqa: BLE001
                pass

    click_input = getattr(item, "click_input", None)
    if callable(click_input):
        try:
            click_input()
            return True
        except Exception:  # noqa: BLE001
            pass

    set_focus = getattr(item, "set_focus", None)
    if callable(set_focus):
        try:
            set_focus()
        except Exception:  # noqa: BLE001
            pass

    type_keys = getattr(item, "type_keys", None)
    if callable(type_keys):
        try:
            type_keys("{ENTER}", set_foreground=False)
            return True
        except Exception:  # noqa: BLE001
            pass

    if callable(send_keys):
        try:
            send_keys("{ENTER}")
            return True
        except Exception:  # noqa: BLE001
            pass

    return False


def _iter_scope_items(scope: object) -> list[object]:
    items: list[object] = []
    if scope is None:
        return items
    items.append(scope)

    descendants = getattr(scope, "descendants", None)
    if callable(descendants):
        try:
            items.extend(descendants())
        except Exception:  # noqa: BLE001
            pass
        return items

    windows = getattr(scope, "windows", None)
    if callable(windows):
        try:
            return list(windows())
        except Exception:  # noqa: BLE001
            return []

    return items


def _safe_windows(desktop: object) -> list[object]:
    windows = getattr(desktop, "windows", None)
    if not callable(windows):
        return []
    try:
        return list(windows())
    except Exception:  # noqa: BLE001
        return []


def _safe_parent(item: object) -> object | None:
    parent = getattr(item, "parent", None)
    if not callable(parent):
        return None
    try:
        return parent()
    except Exception:  # noqa: BLE001
        return None


def _safe_element_name(item: object) -> str:
    element_info = getattr(item, "element_info", None)
    if element_info is not None:
        name = getattr(element_info, "name", "")
        if isinstance(name, str) and name.strip():
            return name.strip()

    window_text = getattr(item, "window_text", None)
    if callable(window_text):
        try:
            text = window_text()
            if isinstance(text, str):
                return text.strip()
        except Exception:  # noqa: BLE001
            return ""
    return ""


def _safe_control_type(item: object) -> str:
    element_info = getattr(item, "element_info", None)
    if element_info is not None:
        control_type = getattr(element_info, "control_type", "")
        if isinstance(control_type, str):
            return control_type.strip()
    friendly_class_name = getattr(item, "friendly_class_name", None)
    if callable(friendly_class_name):
        try:
            text = friendly_class_name()
            if isinstance(text, str):
                return text.strip()
        except Exception:  # noqa: BLE001
            return ""
    return ""


def _match_element_name(candidate: str, names: list[str], *, contains_match: bool) -> bool:
    value = (candidate or "").strip()
    if not value:
        return False
    for expected in names:
        term = (expected or "").strip()
        if not term:
            continue
        if contains_match:
            if term.casefold() in value.casefold():
                return True
            continue
        if value.casefold() == term.casefold():
            return True
    return False


def _contains_any_term(candidate: str, terms: list[str]) -> bool:
    value = (candidate or "").strip()
    if not value:
        return False
    lowered = value.casefold()
    for term in terms:
        normalized = (term or "").strip()
        if normalized and normalized.casefold() in lowered:
            return True
    return False


def _run_settings_ui_script(script: str, *, timeout_sec: int) -> _UiScriptRunResult:
    script_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="\n",
            suffix=".ps1",
            prefix="mouse_bt_ui_",
            delete=False,
        ) as handle:
            script_path = handle.name
            handle.write("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8\n")
            handle.write("$OutputEncoding=[System.Text.Encoding]::UTF8\n")
            handle.write(script.lstrip("\ufeff"))

        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                script_path,
            ],
            capture_output=True,
            text=False,
            timeout=timeout_sec,
            check=False,
            **_build_hidden_subprocess_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _decode_process_bytes(exc.stdout).strip()
        stderr = _decode_process_bytes(exc.stderr).strip()
        if not stderr:
            stderr = f"PowerShell 脚本执行超时（>{timeout_sec}s）。"
        _LOGGER.warning("蓝牙设置页自动化脚本执行超时: %s", stderr)
        return _UiScriptRunResult(stdout=stdout, stderr=stderr, returncode=None)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("蓝牙设置页自动化脚本执行失败: %s", exc)
        return _UiScriptRunResult(stdout="", stderr=str(exc), returncode=None)
    finally:
        if script_path:
            try:
                os.unlink(script_path)
            except OSError:
                pass

    stdout = _decode_process_bytes(completed.stdout).strip()
    stderr = _decode_process_bytes(completed.stderr).strip()
    if completed.returncode != 0:
        detail = (stderr or stdout or f"PowerShell 返回码 {completed.returncode}")[:300]
        _LOGGER.warning(
            "蓝牙设置页自动化脚本执行失败（返回码=%s）: %s",
            completed.returncode,
            detail,
        )
    return _UiScriptRunResult(stdout=stdout, stderr=stderr, returncode=completed.returncode)


def _build_ui_script_no_output_reason(action_label: str, run_result: _UiScriptRunResult) -> str:
    detail = (run_result.stderr or "").strip()
    if detail:
        return f"{action_label}界面自动化未返回结果: {detail[:300]}"
    if run_result.returncode not in (None, 0):
        return f"{action_label}界面自动化未返回结果（PowerShell 返回码 {run_result.returncode}）。"
    return f"{action_label}界面自动化未返回结果。"


def _remove_via_settings_ui_powershell(
    name_keyword: str,
    mac: str,
    *,
    timeout_sec: float,
) -> _SettingsUiActionResult:
    script = _build_settings_remove_script(name_keyword, mac, timeout_sec)
    run_result = _run_settings_ui_script(script, timeout_sec=max(15, int(timeout_sec) + 20))
    output = run_result.stdout
    if not output:
        return _SettingsUiActionResult(
            ok=False,
            reason=_build_ui_script_no_output_reason("蓝牙删除配对", run_result),
        )

    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        detail = output[:300]
        if run_result.stderr:
            detail = f"{detail} | stderr={run_result.stderr[:200]}"
        return _SettingsUiActionResult(
            ok=False,
            reason=f"蓝牙删除配对界面自动化返回了无效结果: {detail}",
        )

    ok = bool(payload.get("ok"))
    reason = str(payload.get("reason") or "")
    selected_name = str(payload.get("selectedName") or "").strip()
    return _SettingsUiActionResult(
        ok=ok,
        reason=reason or ("已完成系统删除配对界面操作。" if ok else "蓝牙删除配对界面操作失败。"),
        selected_name=selected_name,
    )


def _pair_via_settings_ui_powershell(
    name_keyword: str,
    mac: str,
    *,
    timeout_sec: float,
) -> _SettingsUiActionResult:
    script = _build_settings_pair_script(name_keyword, mac, timeout_sec)
    run_result = _run_settings_ui_script(script, timeout_sec=max(15, int(timeout_sec) + 20))
    output = run_result.stdout
    if not output:
        return _SettingsUiActionResult(
            ok=False,
            reason=_build_ui_script_no_output_reason("蓝牙配对", run_result),
        )

    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        detail = output[:300]
        if run_result.stderr:
            detail = f"{detail} | stderr={run_result.stderr[:200]}"
        return _SettingsUiActionResult(
            ok=False,
            reason=f"蓝牙配对界面自动化返回了无效结果: {detail}",
        )

    ok = bool(payload.get("ok"))
    reason = str(payload.get("reason") or "")
    selected_name = str(payload.get("selectedName") or "").strip()
    return _SettingsUiActionResult(
        ok=ok,
        reason=reason or ("已完成系统配对界面操作。" if ok else "蓝牙配对界面操作失败。"),
        selected_name=selected_name,
    )


def _build_settings_pair_script(name_keyword: str, mac: str, timeout_sec: float) -> str:
    template = r'''
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes,System.Windows.Forms

function Write-JsonResult($ok, $reason, $selectedName) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    @{
        ok = $ok
        reason = $reason
        selectedName = $selectedName
    } | ConvertTo-Json -Compress
}

function Test-NameContains([string]$candidate, [string[]]$terms) {
    if ([string]::IsNullOrWhiteSpace($candidate)) { return $false }
    foreach ($term in $terms) {
        if ([string]::IsNullOrWhiteSpace($term)) { continue }
        if ($candidate.IndexOf($term, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            return $true
        }
    }
    return $false
}

function Find-WindowByContains([string[]]$terms) {
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $windows = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($window in $windows) {
        if (Test-NameContains $window.Current.Name $terms) {
            return $window
        }
    }
    return $null
}

function Find-ElementByNames($root, [string[]]$names, [string[]]$controlTypeNames, [bool]$containsMatch) {
    if ($null -eq $root) { return $null }
    $items = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($item in $items) {
        $name = [string]$item.Current.Name
        $typeName = [string]$item.Current.ControlType.ProgrammaticName
        if ($controlTypeNames.Count -gt 0 -and -not ($controlTypeNames -contains $typeName)) {
            continue
        }
        if ($containsMatch) {
            if (Test-NameContains $name $names) {
                return $item
            }
            continue
        }
        foreach ($expected in $names) {
            if ($name -eq $expected) {
                return $item
            }
        }
    }
    return $null
}

function Resolve-InvokableElement($item) {
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $current = $item
    for ($i = 0; $i -lt 6 -and $null -ne $current; $i++) {
        $typeName = [string]$current.Current.ControlType.ProgrammaticName
        if ($typeName -in @(
            'ControlType.Button',
            'ControlType.ListItem',
            'ControlType.Custom',
            'ControlType.Hyperlink'
        )) {
            return $current
        }
        $current = $walker.GetParent($current)
    }
    return $item
}

function Invoke-Element($item) {
    if ($null -eq $item) { return $false }
    $pattern = $null
    $invokePattern = [System.Windows.Automation.InvokePattern]::Pattern
    if ($item.TryGetCurrentPattern($invokePattern, [ref]$pattern)) {
        try {
            ([System.Windows.Automation.InvokePattern]$pattern).Invoke()
            return $true
        } catch {
            $pattern = $null
        }
    }
    $pattern = $null
    $selectionPattern = [System.Windows.Automation.SelectionItemPattern]::Pattern
    if ($item.TryGetCurrentPattern($selectionPattern, [ref]$pattern)) {
        try {
            ([System.Windows.Automation.SelectionItemPattern]$pattern).Select()
            return $true
        } catch {
            $pattern = $null
        }
    }
    $legacyPatternType = [type]::GetType('System.Windows.Automation.LegacyIAccessiblePattern, UIAutomationClient')
    if ($null -ne $legacyPatternType) {
        $pattern = $null
        $legacyPattern = $legacyPatternType::Pattern
        if ($item.TryGetCurrentPattern($legacyPattern, [ref]$pattern)) {
            try {
                $pattern.DoDefaultAction()
                return $true
            } catch {
                $pattern = $null
            }
        }
    }
    try {
        $item.SetFocus()
        [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
        return $true
    } catch {
        return $false
    }
}

function Wait-Window([string[]]$terms, [double]$timeoutSec) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        $window = Find-WindowByContains $terms
        if ($null -ne $window) {
            return $window
        }
        Start-Sleep -Milliseconds 300
    }
    return $null
}

function Wait-Element($root, [string[]]$names, [string[]]$controlTypeNames, [bool]$containsMatch, [double]$timeoutSec) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        $item = Find-ElementByNames $root $names $controlTypeNames $containsMatch
        if ($null -ne $item) {
            return $item
        }
        Start-Sleep -Milliseconds 300
    }
    return $null
}

function Click-OptionalButtons($root, [string[]]$names) {
    $button = Find-ElementByNames $root $names @('ControlType.Button') $false
    if ($null -ne $button) {
        [void](Invoke-Element $button)
        return $true
    }
    return $false
}

try {
    $targetName = __TARGET_NAME__
    $targetMac = __TARGET_MAC__
    $timeoutSec = [double]__TIMEOUT_SEC__

    Start-Process explorer.exe 'ms-settings:bluetooth'

    $settingsWindow = Wait-Window @('Bluetooth', '蓝牙', '设置', 'Settings') 12
    if ($null -eq $settingsWindow) {
        Write-Output (Write-JsonResult $false '未找到系统蓝牙设置窗口。' '')
        exit 0
    }

    $addButton = Wait-Element $settingsWindow @('Add device', '添加设备') @('ControlType.Button') $false 10
    if ($null -eq $addButton) {
        Write-Output (Write-JsonResult $false '未找到“添加设备”按钮。' '')
        exit 0
    }
    if (-not (Invoke-Element $addButton)) {
        Write-Output (Write-JsonResult $false '无法触发“添加设备”按钮。' '')
        exit 0
    }

    $dialog = Wait-Window @('Add a device', '添加设备') 1
    if ($null -eq $dialog) {
        $dialog = $settingsWindow
    }

    $bluetoothButton = Wait-Element $dialog @('Bluetooth', '蓝牙') @('ControlType.Button', 'ControlType.ListItem', 'ControlType.Custom', 'ControlType.Text') $true 6
    if ($null -eq $bluetoothButton) {
        $bluetoothButton = Wait-Element ([System.Windows.Automation.AutomationElement]::RootElement) @('Bluetooth', '蓝牙') @('ControlType.Button', 'ControlType.ListItem', 'ControlType.Custom', 'ControlType.Text') $true 4
    }
    if ($null -eq $bluetoothButton) {
        Write-Output (Write-JsonResult $false '未找到“蓝牙”配对入口。' '')
        exit 0
    }
    if (-not (Invoke-Element $bluetoothButton)) {
        Write-Output (Write-JsonResult $false '无法触发“蓝牙”配对入口。' '')
        exit 0
    }

    $targetTerms = @($targetName)
    if (-not [string]::IsNullOrWhiteSpace($targetMac)) {
        $targetTerms += ($targetMac -replace '[:\-\s]', '')
    }

    $deviceItem = Wait-Element $dialog $targetTerms @('ControlType.ListItem', 'ControlType.Button', 'ControlType.Custom', 'ControlType.Text') $true $timeoutSec
    if ($null -eq $deviceItem) {
        $deviceItem = Wait-Element ([System.Windows.Automation.AutomationElement]::RootElement) $targetTerms @('ControlType.ListItem', 'ControlType.Button', 'ControlType.Custom', 'ControlType.Text') $true 4
    }
    if ($null -eq $deviceItem) {
        Write-Output (Write-JsonResult $false '在“添加设备”界面中未找到目标蓝牙设备。' '')
        exit 0
    }

    $clickable = Resolve-InvokableElement $deviceItem
    $selectedName = [string]$clickable.Current.Name
    if (-not (Invoke-Element $clickable)) {
        Write-Output (Write-JsonResult $false '已找到目标设备，但无法触发配对。' $selectedName)
        exit 0
    }

    $buttonDeadline = (Get-Date).AddSeconds([Math]::Min(6.0, [Math]::Max(2.0, $timeoutSec / 3.0)))
    while ((Get-Date) -lt $buttonDeadline) {
        $clicked = $false
        foreach ($buttonNames in @(
            @('Pair', '配对'),
            @('Connect', '连接'),
            @('Done', '完成'),
            @('Close', '关闭')
        )) {
            if ((Click-OptionalButtons $dialog $buttonNames) -or (Click-OptionalButtons ([System.Windows.Automation.AutomationElement]::RootElement) $buttonNames)) {
                $clicked = $true
                Start-Sleep -Milliseconds 500
            }
        }
        if (-not $clicked) {
            Start-Sleep -Milliseconds 350
        }
    }

    Write-Output (Write-JsonResult $true '' $selectedName)
} catch {
    $message = $_.Exception.Message
    if ([string]::IsNullOrWhiteSpace($message)) {
        $message = $_ | Out-String
    }
    Write-Output (Write-JsonResult $false $message '')
}
'''
    return (
        template.replace("__TARGET_NAME__", _ps_quote(name_keyword))
        .replace("__TARGET_MAC__", _ps_quote(normalize_mac(mac)))
        .replace("__TIMEOUT_SEC__", f"{max(5.0, timeout_sec):.3f}")
    )


def _build_settings_remove_script(name_keyword: str, mac: str, timeout_sec: float) -> str:
    template = r'''
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes,System.Windows.Forms

function Write-JsonResult($ok, $reason, $selectedName) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    @{
        ok = $ok
        reason = $reason
        selectedName = $selectedName
    } | ConvertTo-Json -Compress
}

function Test-NameContains([string]$candidate, [string[]]$terms) {
    if ([string]::IsNullOrWhiteSpace($candidate)) { return $false }
    foreach ($term in $terms) {
        if ([string]::IsNullOrWhiteSpace($term)) { continue }
        if ($candidate.IndexOf($term, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            return $true
        }
    }
    return $false
}

function Find-WindowByContains([string[]]$terms) {
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $windows = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($window in $windows) {
        if (Test-NameContains $window.Current.Name $terms) {
            return $window
        }
    }
    return $null
}

function Find-ElementByNames($root, [string[]]$names, [string[]]$controlTypeNames, [bool]$containsMatch) {
    if ($null -eq $root) { return $null }
    $items = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($item in $items) {
        $name = [string]$item.Current.Name
        $typeName = [string]$item.Current.ControlType.ProgrammaticName
        if ($controlTypeNames.Count -gt 0 -and -not ($controlTypeNames -contains $typeName)) {
            continue
        }
        if ($containsMatch) {
            if (Test-NameContains $name $names) {
                return $item
            }
            continue
        }
        foreach ($expected in $names) {
            if ($name -eq $expected) {
                return $item
            }
        }
    }
    return $null
}

function Find-ElementNearItem($item, [string[]]$names, [string[]]$controlTypeNames) {
    if ($null -eq $item) { return $null }
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $current = $item
    for ($i = 0; $i -lt 6 -and $null -ne $current; $i++) {
        $candidate = Find-ElementByNames $current $names $controlTypeNames $false
        if ($null -ne $candidate) {
            return $candidate
        }
        $current = $walker.GetParent($current)
    }
    return $null
}

function Resolve-InvokableElement($item) {
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $current = $item
    for ($i = 0; $i -lt 6 -and $null -ne $current; $i++) {
        $typeName = [string]$current.Current.ControlType.ProgrammaticName
        if ($typeName -in @(
            'ControlType.Button',
            'ControlType.ListItem',
            'ControlType.Custom',
            'ControlType.Hyperlink',
            'ControlType.DataItem'
        )) {
            return $current
        }
        $current = $walker.GetParent($current)
    }
    return $item
}

function Invoke-Element($item) {
    if ($null -eq $item) { return $false }
    $pattern = $null
    $invokePattern = [System.Windows.Automation.InvokePattern]::Pattern
    if ($item.TryGetCurrentPattern($invokePattern, [ref]$pattern)) {
        try {
            ([System.Windows.Automation.InvokePattern]$pattern).Invoke()
            return $true
        } catch {
            $pattern = $null
        }
    }
    $pattern = $null
    $selectionPattern = [System.Windows.Automation.SelectionItemPattern]::Pattern
    if ($item.TryGetCurrentPattern($selectionPattern, [ref]$pattern)) {
        try {
            ([System.Windows.Automation.SelectionItemPattern]$pattern).Select()
            return $true
        } catch {
            $pattern = $null
        }
    }
    $legacyPatternType = [type]::GetType('System.Windows.Automation.LegacyIAccessiblePattern, UIAutomationClient')
    if ($null -ne $legacyPatternType) {
        $pattern = $null
        $legacyPattern = $legacyPatternType::Pattern
        if ($item.TryGetCurrentPattern($legacyPattern, [ref]$pattern)) {
            try {
                $pattern.DoDefaultAction()
                return $true
            } catch {
                $pattern = $null
            }
        }
    }
    try {
        $item.SetFocus()
        [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
        return $true
    } catch {
        return $false
    }
}

function Wait-Window([string[]]$terms, [double]$timeoutSec) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        $window = Find-WindowByContains $terms
        if ($null -ne $window) {
            return $window
        }
        Start-Sleep -Milliseconds 300
    }
    return $null
}

function Wait-Element($root, [string[]]$names, [string[]]$controlTypeNames, [bool]$containsMatch, [double]$timeoutSec) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        $item = Find-ElementByNames $root $names $controlTypeNames $containsMatch
        if ($null -ne $item) {
            return $item
        }
        Start-Sleep -Milliseconds 300
    }
    return $null
}

try {
    $targetName = __TARGET_NAME__
    $targetMac = __TARGET_MAC__
    $timeoutSec = [double]__TIMEOUT_SEC__

    Start-Process explorer.exe 'ms-settings:bluetooth'

    $settingsWindow = Wait-Window @('Bluetooth', '蓝牙', '设置', 'Settings') 12
    if ($null -eq $settingsWindow) {
        Write-Output (Write-JsonResult $false '未找到系统蓝牙设置窗口。' '')
        exit 0
    }

    $targetTerms = @($targetName)
    if (-not [string]::IsNullOrWhiteSpace($targetMac)) {
        $targetTerms += ($targetMac -replace '[:\-\s]', '')
    }

    $deviceItem = Wait-Element $settingsWindow $targetTerms @('ControlType.ListItem', 'ControlType.Button', 'ControlType.Custom', 'ControlType.Text', 'ControlType.DataItem') $true $timeoutSec
    if ($null -eq $deviceItem) {
        Write-Output (Write-JsonResult $false '在系统蓝牙设备列表中未找到目标设备。' '')
        exit 0
    }

    $clickable = Resolve-InvokableElement $deviceItem
    $selectedName = [string]$clickable.Current.Name
    [void](Invoke-Element $clickable)
    Start-Sleep -Milliseconds 800

    $removeNames = @('Remove device', '删除设备', 'Remove', '删除')
    $removeButton = Find-ElementNearItem $deviceItem $removeNames @('ControlType.Button', 'ControlType.Hyperlink', 'ControlType.MenuItem')
    if ($null -eq $removeButton) {
        $removeButton = Wait-Element $settingsWindow $removeNames @('ControlType.Button', 'ControlType.Hyperlink', 'ControlType.MenuItem') $false 8
    }
    if ($null -eq $removeButton) {
        Write-Output (Write-JsonResult $false '已定位到目标设备，但未找到“删除设备”按钮。' $selectedName)
        exit 0
    }

    if (-not (Invoke-Element $removeButton)) {
        Write-Output (Write-JsonResult $false '已找到“删除设备”按钮，但无法触发。' $selectedName)
        exit 0
    }

    Start-Sleep -Milliseconds 500
    $confirmButton = Wait-Element $settingsWindow @('Yes', '是', 'Remove', '删除', 'Unpair', '取消配对') @('ControlType.Button') $false 8
    if ($null -ne $confirmButton) {
        [void](Invoke-Element $confirmButton)
    }

    Write-Output (Write-JsonResult $true '' $selectedName)
} catch {
    $message = $_.Exception.Message
    if ([string]::IsNullOrWhiteSpace($message)) {
        $message = $_ | Out-String
    }
    Write-Output (Write-JsonResult $false $message '')
}
'''
    return (
        template.replace("__TARGET_NAME__", _ps_quote(name_keyword))
        .replace("__TARGET_MAC__", _ps_quote(normalize_mac(mac)))
        .replace("__TIMEOUT_SEC__", f"{max(5.0, timeout_sec):.3f}")
    )

def _ps_quote(value: str) -> str:
    return "'" + (value or "").replace("'", "''") + "'"


def _log(log_cb: LogCallback, level: str, message: str) -> None:
    if log_cb is not None:
        log_cb(level, message)
        return
    log_level = getattr(logging, level.upper(), logging.INFO)
    _LOGGER.log(log_level, message)




