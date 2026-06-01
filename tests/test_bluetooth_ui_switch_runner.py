from __future__ import annotations

import unittest

from src.core.bluetooth_pairing import BluetoothActionResult
from src.core.bluetooth_ui_switch_runner import (
    BluetoothUiSwitchRunner,
    _click_element,
    _close_window_without_global_hotkey,
    _find_bluetooth_toggle,
    _has_toggle_pattern,
    _read_toggle_state,
)
from src.core.simulators import SimulatedRelay
from src.core.types import BluetoothSwitchSettings


class _FakeBluetoothManager:
    def __init__(self, *, paired: bool, connected: bool) -> None:
        self.paired = paired
        self.connected = connected
        self.pair_calls = 0
        self.remove_calls = 0

    def query_paired_devices(self, name_keyword: str, mac: str, mode: str):
        _ = name_keyword, mac, mode
        return [object()] if self.paired else []

    def is_target_connected(self, name_keyword: str, mac: str, mode: str):
        _ = name_keyword, mac, mode
        return self.connected and self.paired, [object()] if self.paired else []

    def pair_target(
        self,
        name_keyword: str,
        mac: str,
        mode: str,
        *,
        timeout_sec: float,
        sample_interval_sec: float,
        log_cb=None,
    ) -> BluetoothActionResult:
        _ = name_keyword, mac, mode, timeout_sec, sample_interval_sec, log_cb
        self.pair_calls += 1
        self.paired = True
        self.connected = True
        return BluetoothActionResult(ok=True, matched=[object()])

    def remove_target(
        self,
        name_keyword: str,
        mac: str,
        mode: str,
        *,
        timeout_sec: float,
        sample_interval_sec: float,
        log_cb=None,
    ) -> BluetoothActionResult:
        _ = name_keyword, mac, mode, timeout_sec, sample_interval_sec, log_cb
        self.remove_calls += 1
        self.paired = False
        self.connected = False
        return BluetoothActionResult(ok=True, matched=[object()])


class _TestableBluetoothUiSwitchRunner(BluetoothUiSwitchRunner):
    def __init__(
        self,
        relay: SimulatedRelay,
        bluetooth: _FakeBluetoothManager,
        settings: BluetoothSwitchSettings,
        *,
        reconnect_on_enable: bool = True,
    ) -> None:
        super().__init__(relay=relay, bluetooth=bluetooth, settings=settings)
        self.bluetooth = bluetooth
        self.reconnect_on_enable = reconnect_on_enable
        self.open_count = 0
        self.close_count = 0
        self.toggle_targets: list[bool] = []

    def _open_settings_ui(self):  # noqa: ANN202
        self.open_count += 1
        return object()

    def _close_settings_ui(self, session: object) -> None:
        _ = session
        self.close_count += 1

    def _click_bluetooth_toggle(self, session: object, *, enable: bool, phase: str) -> None:
        _ = session, phase
        self.toggle_targets.append(enable)
        self.bluetooth.paired = True
        self.bluetooth.connected = enable and self.reconnect_on_enable

    def _wait_for_connection_state(self, expected_connected: bool, phase: str):
        _ = phase
        connected, matched = self.bluetooth.is_target_connected("SimMouse", "", "name_or_mac")
        return connected == expected_connected, matched

    def _controlled_sleep(self, seconds: float) -> None:
        _ = seconds
        self._ensure_not_stopped()


class _ElementInfo:
    def __init__(self, *, name: str = "", control_type: str = "") -> None:
        self.name = name
        self.control_type = control_type
        self.automation_id = ""


class _NoPatternSwitch:
    def __init__(self) -> None:
        self.element_info = _ElementInfo(name="蓝牙", control_type="Switch")

    @property
    def iface_toggle(self):  # noqa: ANN202
        raise RuntimeError("NoPatternInterfaceError")

    @property
    def click_input(self):  # noqa: ANN202
        raise RuntimeError("NoPatternInterfaceError")


class _BluetoothParent:
    element_info = _ElementInfo(name="蓝牙和其他设备", control_type="Window")


class _CloseSettingsButton:
    element_info = _ElementInfo(name="关闭 设置", control_type="Button")

    def parent(self):
        return _BluetoothParent()

    def invoke(self) -> None:
        raise AssertionError("close button must not be invoked as Bluetooth toggle")


class _WindowWithoutClose:
    def __init__(self) -> None:
        self.hotkey_sent = False

    def top_level_parent(self):
        return self


class _Scope:
    def __init__(self, item: object) -> None:
        self._item = item

    def descendants(self):
        return [self._item]


class TestBluetoothUiSwitchRunner(unittest.TestCase):
    def _settings(self) -> BluetoothSwitchSettings:
        return BluetoothSwitchSettings(
            test_count=1,
            bt_name_keyword="SimMouse",
            bt_match_mode="name_or_mac",
            mode_relay_channel=1,
            pairing_relay_channel=2,
            pairing_press_ms=10,
            state_timeout_ms=1000,
            sample_interval_ms=10,
        )

    def test_runner_repairs_disconnected_precondition_then_runs_full_ui_cycle(self) -> None:
        relay = SimulatedRelay()
        bluetooth = _FakeBluetoothManager(paired=False, connected=False)
        runner = _TestableBluetoothUiSwitchRunner(relay, bluetooth, self._settings())

        summary = runner.run()

        self.assertEqual(summary.success_count, 1)
        self.assertEqual(summary.fail_count, 0)
        self.assertEqual(bluetooth.pair_calls, 1)
        self.assertEqual(runner.toggle_targets, [False, True])
        self.assertEqual(runner.open_count, 1)
        self.assertEqual(runner.close_count, 1)
        self.assertTrue(relay.query_channel_state(1))
        self.assertFalse(relay.query_channel_state(2))

    def test_runner_skips_pairing_when_target_is_already_connected(self) -> None:
        relay = SimulatedRelay()
        bluetooth = _FakeBluetoothManager(paired=True, connected=True)
        runner = _TestableBluetoothUiSwitchRunner(relay, bluetooth, self._settings())

        summary = runner.run()

        self.assertEqual(summary.success_count, 1)
        self.assertEqual(summary.fail_count, 0)
        self.assertEqual(bluetooth.pair_calls, 0)
        self.assertEqual(runner.toggle_targets, [False, True])
        self.assertFalse(relay.query_channel_state(1))

    def test_runner_marks_cycle_failed_when_device_does_not_reconnect(self) -> None:
        relay = SimulatedRelay()
        bluetooth = _FakeBluetoothManager(paired=True, connected=True)
        runner = _TestableBluetoothUiSwitchRunner(
            relay,
            bluetooth,
            self._settings(),
            reconnect_on_enable=False,
        )

        summary = runner.run()

        self.assertEqual(summary.success_count, 0)
        self.assertEqual(summary.fail_count, 1)
        self.assertEqual(runner.toggle_targets, [False, True])
        self.assertEqual(runner.close_count, 1)

    def test_toggle_helpers_ignore_missing_uia_toggle_pattern(self) -> None:
        item = _NoPatternSwitch()

        self.assertIsNone(_read_toggle_state(item))
        self.assertFalse(_has_toggle_pattern(item))
        self.assertFalse(_click_element(item, send_keys=None))
        self.assertIs(_find_bluetooth_toggle([_Scope(item)]), item)

    def test_finder_does_not_treat_settings_close_button_as_toggle(self) -> None:
        self.assertIsNone(_find_bluetooth_toggle([_Scope(_CloseSettingsButton())]))

    def test_close_settings_window_does_not_use_global_hotkey_fallback(self) -> None:
        self.assertFalse(_close_window_without_global_hotkey(_WindowWithoutClose()))


if __name__ == "__main__":
    unittest.main()
