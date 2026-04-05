from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from src.core.bluetooth_pairing import (
    BluetoothActionResult,
    SystemBluetoothManager,
    _build_settings_pair_script,
    _pair_via_settings_ui,
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


    @patch("src.core.bluetooth_pairing._run_settings_ui_script")
    def test_pair_ui_reports_stderr_when_stdout_missing(self, mock_run_settings_ui_script: Mock) -> None:
        mock_run_settings_ui_script.return_value = Mock(
            stdout="",
            stderr="CreateProcess failed",
            returncode=1,
        )

        result = _pair_via_settings_ui("LOWA Mouse", "D5:E7:15:41:4C:B3", timeout_sec=10.0)

        self.assertFalse(result.ok)
        self.assertIn("CreateProcess failed", result.reason)

    def test_pair_script_keeps_expected_localized_keywords(self) -> None:
        script = _build_settings_pair_script("LOWA Mouse", "D5:E7:15:41:4C:B3", 10.0)

        self.assertIn("\u6dfb\u52a0\u8bbe\u5907", script)
        self.assertIn("\u84dd\u7259", script)
        self.assertNotIn("????", script)


if __name__ == "__main__":
    unittest.main()

