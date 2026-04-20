from __future__ import annotations

import ctypes
import logging
import subprocess
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
_SW_RESTORE = 9

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


def _pair_via_settings_ui(name_keyword: str, mac: str, *, timeout_sec: float) -> BluetoothActionResult:
    return _build_pair_ui_action_result(
        _pair_via_settings_ui_pywinauto(
            name_keyword,
            mac,
            timeout_sec=timeout_sec,
        )
    )


def _remove_via_settings_ui(name_keyword: str, mac: str, *, timeout_sec: float) -> BluetoothActionResult:
    return _build_remove_ui_action_result(
        _remove_via_settings_ui_pywinauto(
            name_keyword,
            mac,
            timeout_sec=timeout_sec,
        )
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


def _pair_via_settings_ui_pywinauto(
    name_keyword: str,
    mac: str,
    *,
    timeout_sec: float,
) -> _SettingsUiActionResult:
    desktop_factory, send_keys, import_error = _load_pywinauto_backend()
    if desktop_factory is None or send_keys is None:
        return _SettingsUiActionResult(ok=False, reason=import_error or "pywinauto 不可用。")

    try:
        desktop = desktop_factory(backend="uia")
        settings_window = _open_bluetooth_settings_window(desktop)
        if settings_window is None:
            return _SettingsUiActionResult(ok=False, reason="未找到系统蓝牙设置窗口。")

        add_button = _wait_for_element(
            [settings_window],
            ["Add device", "添加设备", "添加蓝牙或其他设备"],
            ["Button"],
            contains_match=True,
            timeout_sec=10.0,
        )
        if add_button is None:
            return _SettingsUiActionResult(ok=False, reason="未找到“添加设备”按钮。")
        if not _invoke_element(add_button, send_keys):
            return _SettingsUiActionResult(ok=False, reason="无法触发“添加设备”按钮。")

        dialog = _wait_for_window(
            desktop,
            ["Add a device", "添加设备"],
            timeout_sec=3.0,
            activate=True,
        )
        if dialog is None:
            dialog = settings_window
            _activate_window(dialog)

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
                ["Done", "已完成"],
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

        _close_pairing_windows(dialog, settings_window, send_keys)
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

    try:
        desktop = desktop_factory(backend="uia")
        settings_window = _open_bluetooth_settings_window(desktop)
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


def _open_bluetooth_settings_window(desktop: object) -> object | None:
    existing = _wait_for_window(
        desktop,
        ["Bluetooth", "蓝牙", "设置", "Settings"],
        timeout_sec=1.0,
        activate=True,
    )
    if existing is not None:
        return existing

    launch_error = _launch_bluetooth_settings()
    if launch_error:
        _LOGGER.warning("无法打开系统蓝牙设置页: %s", launch_error[:300])
        return None

    return _wait_for_window(
        desktop,
        ["Bluetooth", "蓝牙", "设置", "Settings"],
        timeout_sec=12.0,
        activate=True,
    )


def _wait_for_window(
    desktop: object,
    terms: list[str],
    *,
    timeout_sec: float,
    activate: bool = False,
) -> object | None:
    deadline = time.monotonic() + max(0.1, timeout_sec)
    while time.monotonic() <= deadline:
        for window in _safe_windows(desktop):
            if _contains_any_term(_safe_element_name(window), terms):
                if activate:
                    _activate_window(window)
                return window
        time.sleep(0.3)
    return None


def _activate_window(window: object) -> None:
    top_level = _safe_top_level_window(window)
    if top_level is None:
        return

    _call_window_method(top_level, "restore")
    hwnd = _safe_window_handle(top_level)
    if hwnd:
        try:
            user32 = getattr(getattr(ctypes, "windll", None), "user32", None)
            if user32 is not None:
                user32.ShowWindow(hwnd, _SW_RESTORE)
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
        except Exception:  # noqa: BLE001
            pass

    _call_window_method(top_level, "set_focus")


def _safe_top_level_window(window: object) -> object | None:
    top_level_parent = getattr(window, "top_level_parent", None)
    if callable(top_level_parent):
        try:
            top_level = top_level_parent()
            if top_level is not None:
                return top_level
        except Exception:  # noqa: BLE001
            pass
    return window


def _safe_window_handle(window: object) -> int:
    for candidate in (
        getattr(window, "handle", 0),
        getattr(getattr(window, "element_info", None), "handle", 0),
    ):
        try:
            handle = int(candidate or 0)
        except (TypeError, ValueError):
            continue
        if handle > 0:
            return handle
    return 0


def _call_window_method(window: object, method_name: str) -> bool:
    method = getattr(window, method_name, None)
    if not callable(method):
        return False
    try:
        method()
    except Exception:  # noqa: BLE001
        return False
    return True


def _close_pairing_windows(dialog: object, settings_window: object, send_keys: object) -> None:
    dialog_window = _safe_top_level_window(dialog)
    settings_top_level = _safe_top_level_window(settings_window)

    if dialog_window is not None and dialog_window is not settings_top_level:
        _close_window(dialog_window, send_keys)
        time.sleep(0.2)

    if settings_top_level is not None:
        _close_window(settings_top_level, send_keys)


def _close_window(window: object, send_keys: object) -> bool:
    top_level = _safe_top_level_window(window)
    if top_level is None:
        return False

    _activate_window(top_level)
    if _call_window_method(top_level, "close"):
        return True

    close_button = _find_element(
        top_level,
        ["Close", "关闭"],
        ["Button", "TitleBar"],
        contains_match=True,
    )
    if close_button is not None and _invoke_element(close_button, send_keys):
        return True

    if callable(send_keys):
        try:
            send_keys("%{F4}")
            return True
        except Exception:  # noqa: BLE001
            return False
    return False


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


def _log(log_cb: LogCallback, level: str, message: str) -> None:
    if log_cb is not None:
        log_cb(level, message)
        return
    log_level = getattr(logging, level.upper(), logging.INFO)
    _LOGGER.log(log_level, message)




