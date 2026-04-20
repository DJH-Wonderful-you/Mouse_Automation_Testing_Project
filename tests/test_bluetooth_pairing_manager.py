from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from src.core.bluetooth_pairing import (
    BluetoothActionResult,
    SystemBluetoothManager,
    _close_pairing_windows,
    _open_bluetooth_settings_window,
    _pair_via_settings_ui,
    _remove_via_settings_ui,
    _wait_for_window,
)
from src.core.bluetooth_probe import BluetoothDeviceInfo


class TestBluetoothPairingManager(unittest.TestCase):
    def _device(self) -> BluetoothDeviceInfo:
        return BluetoothDeviceInfo(
            name="LOWA Mouse",
            instance_id=r"BTHLE\Dev_d5e715414cb2\7&1679758c&0&d5e715414cb2",
            status="OK",
            class_name="Bluetooth",
            present=True,
            mac="D5:E7:15:41:4C:B2",
            connected=True,
        )

    @patch("src.core.bluetooth_pairing._remove_device_instance")
    @patch("src.core.bluetooth_pairing._remove_via_settings_ui")
    def test_remove_target_prefers_settings_ui_when_name_available(
        self,
        mock_remove_via_settings_ui: Mock,
        mock_remove_device_instance: Mock,
    ) -> None:
        manager = SystemBluetoothManager(probe=Mock())
        device = self._device()
        manager.query_paired_devices = Mock(side_effect=[[device], []])  # type: ignore[method-assign]
        mock_remove_via_settings_ui.return_value = BluetoothActionResult(ok=True, reason="ui ok")

        result = manager.remove_target(
            name_keyword="LOWA Mouse",
            mac=device.mac,
            mode="name_and_mac",
            timeout_sec=0.2,
            sample_interval_sec=0.01,
        )

        self.assertTrue(result.ok)
        mock_remove_via_settings_ui.assert_called_once()
        mock_remove_device_instance.assert_not_called()

    @patch("src.core.bluetooth_pairing._remove_device_instance")
    @patch("src.core.bluetooth_pairing._remove_via_settings_ui")
    def test_remove_target_falls_back_to_pnputil_when_settings_ui_fails(
        self,
        mock_remove_via_settings_ui: Mock,
        mock_remove_device_instance: Mock,
    ) -> None:
        manager = SystemBluetoothManager(probe=Mock())
        device = self._device()
        manager.query_paired_devices = Mock(side_effect=[[device], []])  # type: ignore[method-assign]
        mock_remove_via_settings_ui.return_value = BluetoothActionResult(ok=False, reason="ui failed")
        mock_remove_device_instance.return_value = ""

        result = manager.remove_target(
            name_keyword="LOWA Mouse",
            mac=device.mac,
            mode="name_and_mac",
            timeout_sec=0.2,
            sample_interval_sec=0.01,
        )

        self.assertTrue(result.ok)
        mock_remove_via_settings_ui.assert_called_once()
        mock_remove_device_instance.assert_called_once_with(device.instance_id)

    @patch("src.core.bluetooth_pairing._remove_device_instance")
    @patch("src.core.bluetooth_pairing._remove_via_settings_ui")
    def test_remove_target_reports_admin_hint_on_access_denied(
        self,
        mock_remove_via_settings_ui: Mock,
        mock_remove_device_instance: Mock,
    ) -> None:
        manager = SystemBluetoothManager(probe=Mock())
        device = self._device()
        manager.query_paired_devices = Mock(return_value=[device])  # type: ignore[method-assign]
        mock_remove_via_settings_ui.return_value = BluetoothActionResult(ok=False, reason="ui failed")
        mock_remove_device_instance.return_value = "无法删除设备\n拒绝访问。"

        result = manager.remove_target(
            name_keyword="LOWA Mouse",
            mac=device.mac,
            mode="name_and_mac",
            timeout_sec=0.2,
            sample_interval_sec=0.01,
        )

        self.assertFalse(result.ok)
        self.assertIn("管理员权限", result.reason)
        self.assertIn("设置页删除配对失败", result.reason)

    @patch("src.core.bluetooth_pairing._pair_via_settings_ui_pywinauto")
    def test_pair_ui_returns_pywinauto_error_when_pywinauto_fails(
        self,
        mock_pair_via_settings_ui_pywinauto: Mock,
    ) -> None:
        mock_pair_via_settings_ui_pywinauto.return_value = Mock(ok=False, reason="pywinauto failed")

        result = _pair_via_settings_ui("LOWA Mouse", "D5:E7:15:41:4C:B3", timeout_sec=10.0)

        self.assertFalse(result.ok)
        self.assertEqual("pywinauto failed", result.reason)
        mock_pair_via_settings_ui_pywinauto.assert_called_once()

    @patch("src.core.bluetooth_pairing._remove_via_settings_ui_pywinauto")
    def test_remove_ui_returns_pywinauto_error_when_pywinauto_fails(
        self,
        mock_remove_via_settings_ui_pywinauto: Mock,
    ) -> None:
        mock_remove_via_settings_ui_pywinauto.return_value = Mock(ok=False, reason="pywinauto failed")

        result = _remove_via_settings_ui("LOWA Mouse", "D5:E7:15:41:4C:B3", timeout_sec=10.0)

        self.assertFalse(result.ok)
        self.assertEqual("pywinauto failed", result.reason)

    @patch("src.core.bluetooth_pairing._activate_window")
    def test_wait_for_window_activates_window_when_requested(
        self,
        mock_activate_window: Mock,
    ) -> None:
        window = Mock()
        window.element_info = Mock()
        window.element_info.name = "Bluetooth Settings"
        desktop = Mock()
        desktop.windows.return_value = [window]

        result = _wait_for_window(
            desktop,
            ["Bluetooth", "蓝牙"],
            timeout_sec=0.1,
            activate=True,
        )

        self.assertIs(result, window)
        mock_activate_window.assert_called_once_with(window)

    @patch("src.core.bluetooth_pairing._launch_bluetooth_settings")
    @patch("src.core.bluetooth_pairing._wait_for_window")
    def test_open_settings_window_reuses_existing_window(
        self,
        mock_wait_for_window: Mock,
        mock_launch_bluetooth_settings: Mock,
    ) -> None:
        existing_window = Mock()
        mock_wait_for_window.return_value = existing_window

        result = _open_bluetooth_settings_window(Mock())

        self.assertIs(result, existing_window)
        mock_launch_bluetooth_settings.assert_not_called()

    @patch("src.core.bluetooth_pairing.time.sleep")
    @patch("src.core.bluetooth_pairing._close_window")
    @patch("src.core.bluetooth_pairing._safe_top_level_window")
    def test_close_pairing_windows_closes_dialog_then_settings(
        self,
        mock_safe_top_level_window: Mock,
        mock_close_window: Mock,
        mock_sleep: Mock,
    ) -> None:
        dialog = Mock(name="dialog")
        settings = Mock(name="settings")
        mock_safe_top_level_window.side_effect = [dialog, settings]

        _close_pairing_windows(dialog, settings, Mock())

        self.assertEqual(
            mock_close_window.call_args_list,
            [
                unittest.mock.call(dialog, unittest.mock.ANY),
                unittest.mock.call(settings, unittest.mock.ANY),
            ],
        )
        mock_sleep.assert_called_once_with(0.2)


if __name__ == "__main__":
    unittest.main()

