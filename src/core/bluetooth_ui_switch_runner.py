from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
import time
from typing import Callable, Protocol

from src.core.bluetooth_pairing import (
    BluetoothActionResult,
    BluetoothManager,
    _activate_window,
    _invoke_element,
    _iter_scope_items,
    _load_pywinauto_backend,
    _open_bluetooth_settings_window,
    _safe_control_type as _pairing_safe_control_type,
    _safe_element_name as _pairing_safe_element_name,
    _safe_parent as _pairing_safe_parent,
)
from src.core.types import BluetoothConnectCycleResult, BluetoothSwitchSettings, RunSummary

_LOGGER = logging.getLogger("bluetooth.ui_switch_runner")

_BLUETOOTH_TERMS = ("Bluetooth", "蓝牙")
_TOGGLE_CONTROL_TYPES = {
    "Button",
    "CheckBox",
    "Custom",
    "ListItem",
    "Pane",
    "Switch",
    "ToggleButton",
}


class RelayController(Protocol):
    def query_channel_state(self, channel: int) -> bool: ...

    def set_channel_state(self, channel: int, on: bool) -> None: ...


class StopRequested(Exception):
    pass


class NonRecoverableError(Exception):
    pass


class UiActionError(Exception):
    pass


@dataclass(slots=True)
class _SettingsUiSession:
    desktop: object
    send_keys: object
    window: object


class BluetoothUiSwitchRunner:
    def __init__(
        self,
        relay: RelayController,
        bluetooth: BluetoothManager,
        settings: BluetoothSwitchSettings,
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
        total = max(0, self._settings.test_count)
        success_count = 0
        fail_count = 0
        self._log("INFO", f"蓝牙 UI 开关测试开始，总轮次: {total}")

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
            except Exception as exc:  # noqa: BLE001
                error_text = _format_exception(exc)
                self._log("ERROR", f"单轮执行异常，记失败并继续: {error_text}")
                result = BluetoothConnectCycleResult(
                    index=index,
                    success=False,
                    reason=f"执行异常: {error_text}",
                )

            if result.success:
                success_count += 1
            else:
                fail_count += 1
            self._emit_cycle(result)
            self._emit_progress(success_count + fail_count, total)

        done = success_count + fail_count
        success_rate = (success_count / done * 100.0) if done else 0.0
        self._log(
            "INFO",
            f"蓝牙 UI 开关测试结束：成功 {success_count}，失败 {fail_count}，成功率 {success_rate:.2f}%",
        )
        return RunSummary(success_count, fail_count, success_rate)

    def _ensure_preconditions(self) -> None:
        connected, matched = self._check_target_connected_once("测试前置检查")
        if connected:
            self._log(
                "INFO",
                f"测试前置检查通过：目标蓝牙设备已连接，匹配到 {len(matched)} 个设备。",
            )
            return

        self._log("WARNING", "目标蓝牙设备未连接，开始执行自动连接前置修复。")
        self._ensure_mode_channel_on(None)
        paired = self._bluetooth.query_paired_devices(
            self._settings.bt_name_keyword,
            self._settings.bt_mac,
            self._settings.bt_match_mode,
        )
        if paired:
            self._log(
                "WARNING",
                "目标蓝牙设备已配对但未连接，将先删除旧配对并重新连接。",
            )
            remove_result = self._remove_target_device(None, phase="前置条件修复")
            if not remove_result.ok:
                raise NonRecoverableError(
                    f"目标蓝牙设备未连接，且删除旧配对失败: {remove_result.reason}"
                )
        else:
            self._log("WARNING", "未检测到目标蓝牙设备处于已配对连接状态，将执行设备连接。")

        pair_result = self._pair_target_device(None, phase="前置条件修复")
        if not pair_result.ok:
            raise NonRecoverableError(f"前置条件自动连接失败: {pair_result.reason}")

        connected, matched = self._wait_for_connection_state(
            True,
            "前置条件连接后检查",
        )
        if not connected:
            raise NonRecoverableError(
                "前置条件自动连接完成，但仍未检测到目标蓝牙设备已连接。"
            )
        self._log(
            "INFO",
            f"前置条件自动连接成功：匹配到 {len(matched)} 个设备，开始正式循环测试。",
        )

    def _run_single_cycle(self, index: int) -> BluetoothConnectCycleResult:
        self._log("INFO", f"[第{index}轮] 开始蓝牙 UI 开关测试。")
        session: _SettingsUiSession | None = None
        bluetooth_confirmed_off = False
        bluetooth_enable_clicked = False
        try:
            session = self._open_settings_ui()

            self._click_bluetooth_toggle(
                session,
                enable=False,
                phase=f"[第{index}轮] 关闭电脑蓝牙",
            )
            disconnected, _ = self._wait_for_connection_state(
                False,
                f"[第{index}轮] 关闭蓝牙后断开检查",
            )
            if not disconnected:
                return BluetoothConnectCycleResult(
                    index=index,
                    success=False,
                    reason="关闭电脑蓝牙后，目标设备未断开连接。",
                )
            bluetooth_confirmed_off = True

            self._click_bluetooth_toggle(
                session,
                enable=True,
                phase=f"[第{index}轮] 打开电脑蓝牙",
            )
            bluetooth_enable_clicked = True
            reconnected, _ = self._wait_for_connection_state(
                True,
                f"[第{index}轮] 打开蓝牙后回连检查",
            )
            if not reconnected:
                return BluetoothConnectCycleResult(
                    index=index,
                    success=False,
                    reason="打开电脑蓝牙后，目标设备未重新连接。",
                )

            return BluetoothConnectCycleResult(
                index=index,
                success=True,
                reason="关闭蓝牙断开、打开蓝牙回连均通过。",
            )
        finally:
            if session is not None and bluetooth_confirmed_off and not bluetooth_enable_clicked:
                self._restore_bluetooth_if_needed(session, index)
            if session is not None:
                self._close_settings_ui(session)

    def _restore_bluetooth_if_needed(self, session: _SettingsUiSession, index: int) -> None:
        connected, _ = self._check_target_connected_once(f"[第{index}轮] 关闭窗口前状态确认")
        if connected:
            return

        try:
            self._log("WARNING", f"[第{index}轮] 目标设备仍未连接，关闭窗口前尝试恢复蓝牙开启。")
            self._click_bluetooth_toggle(
                session,
                enable=True,
                phase=f"[第{index}轮] 异常恢复打开电脑蓝牙",
            )
        except Exception as exc:  # noqa: BLE001
            self._log("WARNING", f"[第{index}轮] 恢复蓝牙开启失败: {exc}")

    def _open_settings_ui(self) -> _SettingsUiSession:
        self._ensure_not_stopped()
        desktop_factory, send_keys, import_error = _load_pywinauto_backend()
        if desktop_factory is None or send_keys is None:
            raise UiActionError(import_error or "pywinauto 不可用。")

        desktop = desktop_factory(backend="uia")
        settings_window = _open_bluetooth_settings_window(desktop)
        if settings_window is None:
            raise UiActionError("未找到系统蓝牙设置窗口。")
        _activate_window(settings_window)
        self._controlled_sleep(0.5)
        return _SettingsUiSession(desktop=desktop, send_keys=send_keys, window=settings_window)

    def _close_settings_ui(self, session: _SettingsUiSession) -> None:
        if not _close_window_without_global_hotkey(session.window):
            self._log("WARNING", "关闭蓝牙设置窗口失败，已跳过 Alt+F4 兜底以避免误关主程序。")
        self._controlled_sleep(0.5)

    def _click_bluetooth_toggle(
        self,
        session: _SettingsUiSession,
        *,
        enable: bool,
        phase: str,
    ) -> None:
        self._ensure_not_stopped()
        target_text = "开启" if enable else "关闭"
        toggle = self._wait_for_bluetooth_toggle(session)
        if toggle is None:
            if self._click_bluetooth_toggle_by_position(session, phase=phase):
                self._controlled_sleep(0.5)
                return
            raise UiActionError(f"{phase}: 未找到系统蓝牙开关控件。")

        control_name = _element_name(toggle) or _automation_id(toggle) or "蓝牙开关"
        state = _read_toggle_state(toggle)
        if state is not None:
            current_text = "开启" if state else "关闭"
            self._log("INFO", f"{phase}: 当前蓝牙开关状态为{current_text}。")
            if state == enable:
                self._log("INFO", f"{phase}: 蓝牙开关已处于目标状态，跳过点击。")
                return

        self._log("INFO", f"{phase}: 点击“{control_name}”控件，目标状态为{target_text}。")
        if not _click_element(toggle, session.send_keys):
            if not self._click_bluetooth_toggle_by_position(session, phase=phase):
                raise UiActionError(f"{phase}: 无法点击系统蓝牙开关控件。")

        if state is not None and not self._wait_for_toggle_state(toggle, enable, 3.0):
            self._log("WARNING", f"{phase}: 蓝牙开关控件状态未在短时间内变为{target_text}。")
        self._controlled_sleep(0.5)

    def _click_bluetooth_toggle_by_position(
        self,
        session: _SettingsUiSession,
        *,
        phase: str,
    ) -> bool:
        rect = _rectangle(session.window)
        if rect is None:
            return False

        left = _rect_left(rect)
        top = _rect_top(rect)
        right = _rect_right(rect)
        bottom = _rect_bottom(rect)
        width = max(1, right - left)
        height = max(1, bottom - top)
        x = max(1, min(width - 1, width - 64))
        y = max(1, min(height - 1, 183))

        self._log(
            "WARNING",
            f"{phase}: 未通过 UIA 定位到蓝牙开关，改用窗口相对坐标点击开关区域。",
        )

        click_input = getattr(session.window, "click_input", None)
        if callable(click_input):
            try:
                click_input(coords=(x, y))
                return True
            except Exception:  # noqa: BLE001
                pass

        try:
            from pywinauto import mouse

            mouse.click(button="left", coords=(left + x, top + y))
            return True
        except Exception as exc:  # noqa: BLE001
            self._log("WARNING", f"{phase}: 坐标点击蓝牙开关区域失败: {_format_exception(exc)}")
            return False

    def _wait_for_bluetooth_toggle(self, session: _SettingsUiSession) -> object | None:
        deadline = time.monotonic() + 10.0
        while time.monotonic() <= deadline:
            self._ensure_not_stopped()
            toggle = _find_bluetooth_toggle([session.window, session.desktop])
            if toggle is not None:
                return toggle
            self._controlled_sleep(0.3)
        return None

    def _wait_for_toggle_state(self, toggle: object, expected_enabled: bool, timeout_sec: float) -> bool:
        deadline = time.monotonic() + max(0.1, timeout_sec)
        while time.monotonic() <= deadline:
            self._ensure_not_stopped()
            state = _read_toggle_state(toggle)
            if state is None:
                return True
            if state == expected_enabled:
                return True
            self._controlled_sleep(0.2)
        return False

    def _check_target_connected_once(self, phase: str) -> tuple[bool, list[object]]:
        self._ensure_not_stopped()
        connected, matched = self._bluetooth.is_target_connected(
            self._settings.bt_name_keyword,
            self._settings.bt_mac,
            self._settings.bt_match_mode,
        )
        matched_list = list(matched)
        status_text = "已连接" if connected else "未连接"
        self._log("INFO", f"{phase}: 蓝牙连接状态 {status_text}，匹配设备 {len(matched_list)} 个。")
        return connected, matched_list

    def _wait_for_connection_state(
        self,
        expected_connected: bool,
        phase: str,
    ) -> tuple[bool, list[object]]:
        deadline = time.monotonic() + max(1.0, self._settings.state_timeout_ms / 1000.0)
        interval = max(0.1, self._settings.sample_interval_ms / 1000.0)
        last_connected = False
        last_matched: list[object] = []
        while time.monotonic() <= deadline:
            self._ensure_not_stopped()
            connected, matched = self._bluetooth.is_target_connected(
                self._settings.bt_name_keyword,
                self._settings.bt_mac,
                self._settings.bt_match_mode,
            )
            last_connected = connected
            last_matched = list(matched)
            if connected == expected_connected:
                status_text = "已连接" if connected else "未连接"
                self._log("INFO", f"{phase}: 目标设备状态已达到预期（{status_text}）。")
                return True, last_matched
            self._controlled_sleep(interval)

        expected_text = "已连接" if expected_connected else "未连接"
        actual_text = "已连接" if last_connected else "未连接"
        self._log(
            "WARNING",
            f"{phase}: 等待目标设备变为{expected_text}超时，最后状态为{actual_text}。",
        )
        return False, last_matched

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
        self,
        index: int | None,
        phase: str,
    ) -> BluetoothActionResult:
        self._ensure_not_stopped()
        prefix = "[前置条件]" if index is None else f"[第{index}轮]"
        self._log("INFO", f"{prefix} {phase}: 开始删除已配对蓝牙设备。")
        return self._bluetooth.remove_target(
            self._settings.bt_name_keyword,
            self._settings.bt_mac,
            self._settings.bt_match_mode,
            timeout_sec=max(1.0, self._settings.state_timeout_ms / 1000.0),
            sample_interval_sec=max(0.1, self._settings.sample_interval_ms / 1000.0),
            log_cb=self._log,
        )

    def _pair_target_device(
        self,
        index: int | None,
        phase: str,
    ) -> BluetoothActionResult:
        prefix = "[前置条件]" if index is None else f"[第{index}轮]"
        self._log("INFO", f"{prefix} {phase}: 开始添加并连接蓝牙设备。")
        self._trigger_pairing_mode(index)
        return self._bluetooth.pair_target(
            self._settings.bt_name_keyword,
            self._settings.bt_mac,
            self._settings.bt_match_mode,
            timeout_sec=max(1.0, self._settings.state_timeout_ms / 1000.0),
            sample_interval_sec=max(0.1, self._settings.sample_interval_ms / 1000.0),
            log_cb=self._log,
        )

    def _set_channel_state(self, channel: int, on: bool, phase: str) -> None:
        action = "打开" if on else "关闭"
        self._ensure_not_stopped()
        try:
            self._relay.set_channel_state(channel, on)
            self._log("INFO", f"{phase}: 通道{channel}{action}命令已发送。")
        except Exception as exc:
            raise NonRecoverableError(f"继电器通道{channel}{action}失败: {exc}") from exc

    def _controlled_sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
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
        _LOGGER.log(getattr(logging, level.upper(), logging.INFO), message)

    def _emit_progress(self, done: int, total: int) -> None:
        if self._progress_cb:
            self._progress_cb(done, total)

    def _emit_cycle(self, result: BluetoothConnectCycleResult) -> None:
        if self._cycle_cb:
            self._cycle_cb(result)


def _find_bluetooth_toggle(scopes: list[object]) -> object | None:
    candidates: list[tuple[int, int, object]] = []
    fallback_candidates: list[object] = []
    seen: set[int] = set()
    for scope in scopes:
        for item in _iter_items(scope):
            marker = id(item)
            if marker in seen:
                continue
            seen.add(marker)
            control_type = _control_type(item)
            if control_type not in _TOGGLE_CONTROL_TYPES:
                continue
            if _is_excluded_toggle_candidate(item, control_type):
                continue
            if _is_toggle_candidate(item, control_type):
                fallback_candidates.append(item)
            score = _score_bluetooth_toggle_candidate(item, control_type)
            if score is None:
                continue
            candidates.append((score, len(_element_name(item)), item))

    if candidates:
        candidates.sort(key=lambda candidate: (candidate[0], candidate[1]))
        return candidates[0][2]

    row_toggle = _find_toggle_near_bluetooth_label(scopes)
    if row_toggle is not None:
        return row_toggle

    if len(fallback_candidates) == 1:
        return fallback_candidates[0]

    if fallback_candidates:
        fallback_candidates.sort(key=_toggle_position_score)
        return fallback_candidates[0]
    candidates.sort(key=lambda candidate: (candidate[0], candidate[1]))
    return None


def _score_bluetooth_toggle_candidate(item: object, control_type: str) -> int | None:
    if _is_excluded_toggle_candidate(item, control_type):
        return None

    name_has_term = _contains_bluetooth_term(_element_name(item))
    automation_has_term = _contains_bluetooth_term(_automation_id(item))
    context_has_term = name_has_term or automation_has_term or _parent_has_bluetooth_context(item)
    if not context_has_term:
        return None

    has_toggle = _has_toggle_pattern(item)
    if has_toggle and name_has_term:
        return 0
    if has_toggle:
        return 1
    if control_type in {"Button", "CheckBox"} and name_has_term:
        return 2
    if control_type in {"Custom", "ListItem"} and name_has_term:
        return 4
    return None


def _parent_has_bluetooth_context(item: object) -> bool:
    current = item
    for _ in range(4):
        current = _parent(current)
        if current is None:
            return False
        if _contains_bluetooth_term(_element_name(current)):
            return True
        if _contains_bluetooth_term(_automation_id(current)):
            return True
    return False


def _find_toggle_near_bluetooth_label(scopes: list[object]) -> object | None:
    best: tuple[int, object] | None = None
    for scope in scopes:
        items = _iter_items(scope)
        bluetooth_items = [
            item
            for item in items
            if _contains_bluetooth_term(_element_name(item))
            or _contains_bluetooth_term(_automation_id(item))
        ]
        for label in bluetooth_items:
            label_rect = _rectangle(label)
            if label_rect is None:
                continue

            search_root = label
            for _ in range(3):
                parent = _parent(search_root)
                if parent is None:
                    break
                search_root = parent

            for candidate in _iter_items(search_root):
                control_type = _control_type(candidate)
                if control_type not in _TOGGLE_CONTROL_TYPES:
                    continue
                if _is_excluded_toggle_candidate(candidate, control_type):
                    continue
                if not _is_toggle_candidate(candidate, control_type):
                    continue
                candidate_rect = _rectangle(candidate)
                if candidate_rect is None:
                    continue
                distance = _row_distance(label_rect, candidate_rect)
                if best is None or distance < best[0]:
                    best = (distance, candidate)
    return best[1] if best is not None else None


def _is_toggle_candidate(item: object, control_type: str) -> bool:
    if _is_excluded_toggle_candidate(item, control_type):
        return False
    return (
        _has_toggle_pattern(item)
        or control_type in {"Switch", "ToggleButton", "CheckBox"}
        or _read_toggle_state(item) is not None
    )


def _is_excluded_toggle_candidate(item: object, control_type: str) -> bool:
    name = _element_name(item).strip().casefold()
    automation_id = _automation_id(item).strip().casefold()
    if control_type in {"TitleBar", "MenuItem"}:
        return True

    blocked_exact = {
        "close",
        "关闭",
        "关闭 设置",
        "minimize",
        "最小化",
        "maximize",
        "最大化",
        "restore",
        "还原",
        "back",
        "后退",
    }
    if name in {item.casefold() for item in blocked_exact}:
        return True

    blocked_fragments = (
        "close",
        "关闭 设置",
        "minimize",
        "maximize",
        "restore",
        "titlebar",
        "caption",
    )
    return any(fragment in name or fragment in automation_id for fragment in blocked_fragments)


def _has_toggle_pattern(item: object) -> bool:
    if _read_toggle_state(item) is not None:
        return True
    return callable(_safe_attr(item, "toggle")) or _safe_attr(item, "iface_toggle") is not None


def _click_element(item: object, send_keys: object) -> bool:
    click_input = _safe_attr(item, "click_input")
    if callable(click_input):
        try:
            click_input()
            return True
        except Exception:  # noqa: BLE001
            pass

    toggle = _safe_attr(item, "toggle")
    if callable(toggle):
        try:
            toggle()
            return True
        except Exception:  # noqa: BLE001
            pass

    try:
        return _invoke_element(item, send_keys)
    except Exception:  # noqa: BLE001
        return False


def _read_toggle_state(item: object) -> bool | None:
    for method_name in ("get_toggle_state", "get_check_state"):
        method = _safe_attr(item, method_name)
        if not callable(method):
            continue
        try:
            state = _coerce_toggle_state(method())
        except Exception:  # noqa: BLE001
            continue
        if state is not None:
            return state

    is_checked = _safe_attr(item, "is_checked")
    if callable(is_checked):
        try:
            state = _coerce_toggle_state(is_checked())
        except Exception:  # noqa: BLE001
            state = None
        if state is not None:
            return state

    iface_toggle = _safe_attr(item, "iface_toggle")
    if iface_toggle is not None:
        try:
            state = _coerce_toggle_state(_safe_attr(iface_toggle, "CurrentToggleState"))
        except Exception:  # noqa: BLE001
            state = None
        if state is not None:
            return state

    get_properties = _safe_attr(item, "get_properties")
    if callable(get_properties):
        try:
            properties = get_properties()
        except Exception:  # noqa: BLE001
            properties = {}
        if isinstance(properties, dict):
            for key in ("toggle_state", "ToggleState", "checked", "is_checked"):
                if key in properties:
                    state = _coerce_toggle_state(properties[key])
                    if state is not None:
                        return state

    return None


def _coerce_toggle_state(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value == 0:
            return False
        if value == 1:
            return True
        return None
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"on", "checked", "true", "1", "开", "开启", "已打开"}:
            return True
        if normalized in {"off", "unchecked", "false", "0", "关", "关闭", "已关闭"}:
            return False
    return None


def _contains_bluetooth_term(value: str) -> bool:
    normalized = (value or "").casefold()
    return any(term.casefold() in normalized for term in _BLUETOOTH_TERMS)


def _format_exception(exc: BaseException) -> str:
    message = str(exc).strip()
    if message:
        return message
    return exc.__class__.__name__


def _close_window_without_global_hotkey(window: object) -> bool:
    top_level = _top_level_window(window)
    if top_level is None:
        return False

    _activate_window(top_level)
    close = _safe_attr(top_level, "close")
    if callable(close):
        try:
            close()
            return True
        except Exception:  # noqa: BLE001
            return False
    return False


def _iter_items(scope: object) -> list[object]:
    try:
        return _iter_scope_items(scope)
    except Exception:  # noqa: BLE001
        return [scope] if scope is not None else []


def _element_name(item: object) -> str:
    try:
        return _pairing_safe_element_name(item)
    except Exception:  # noqa: BLE001
        return ""


def _control_type(item: object) -> str:
    try:
        return _pairing_safe_control_type(item)
    except Exception:  # noqa: BLE001
        return ""


def _parent(item: object) -> object | None:
    try:
        return _pairing_safe_parent(item)
    except Exception:  # noqa: BLE001
        return None


def _top_level_window(item: object) -> object | None:
    top_level_parent = _safe_attr(item, "top_level_parent")
    if callable(top_level_parent):
        try:
            top_level = top_level_parent()
            if top_level is not None:
                return top_level
        except Exception:  # noqa: BLE001
            pass
    return item


def _automation_id(item: object) -> str:
    element_info = _safe_attr(item, "element_info")
    if element_info is None:
        return ""
    automation_id = _safe_attr(element_info, "automation_id", "")
    if isinstance(automation_id, str):
        return automation_id.strip()
    return ""


def _rectangle(item: object) -> object | None:
    rectangle = _safe_attr(item, "rectangle")
    if callable(rectangle):
        try:
            return rectangle()
        except Exception:  # noqa: BLE001
            pass
    element_info = _safe_attr(item, "element_info")
    return _safe_attr(element_info, "rectangle") if element_info is not None else None


def _safe_attr(item: object, name: str, default: object | None = None) -> object | None:
    try:
        return getattr(item, name, default)
    except Exception:  # noqa: BLE001
        return default


def _rect_value(rect: object, name: str) -> int | None:
    try:
        value = getattr(rect, name)
    except Exception:  # noqa: BLE001
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rect_center_y(rect: object) -> int:
    top = _rect_value(rect, "top") or 0
    bottom = _rect_value(rect, "bottom") or top
    return (top + bottom) // 2


def _rect_left(rect: object) -> int:
    return _rect_value(rect, "left") or 0


def _rect_top(rect: object) -> int:
    return _rect_value(rect, "top") or 0


def _rect_right(rect: object) -> int:
    return _rect_value(rect, "right") or _rect_left(rect)


def _rect_bottom(rect: object) -> int:
    return _rect_value(rect, "bottom") or _rect_top(rect)


def _row_distance(label_rect: object, candidate_rect: object) -> int:
    vertical = abs(_rect_center_y(label_rect) - _rect_center_y(candidate_rect))
    right_bonus = 0 if _rect_left(candidate_rect) >= _rect_right(label_rect) else 1000
    horizontal = max(0, _rect_left(candidate_rect) - _rect_right(label_rect))
    return vertical * 20 + right_bonus + horizontal


def _toggle_position_score(item: object) -> tuple[int, int]:
    rect = _rectangle(item)
    if rect is None:
        return (999999, 999999)
    return (_rect_center_y(rect), -_rect_left(rect))
