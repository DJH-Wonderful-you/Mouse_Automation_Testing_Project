from __future__ import annotations

import unittest

from src.core.bluetooth_probe import (
    BluetoothDeviceInfo,
    extract_mac,
    is_paired_bluetooth_instance,
    is_primary_device_node,
    match_target,
    normalize_mac,
)


class TestBluetoothMatcher(unittest.TestCase):
    def test_normalize_mac(self) -> None:
        self.assertEqual(normalize_mac("aa-bb-cc-11-22-33"), "AA:BB:CC:11:22:33")
        self.assertEqual(normalize_mac("AABBCC112233"), "AA:BB:CC:11:22:33")
        self.assertEqual(normalize_mac("invalid"), "")

    def test_extract_mac(self) -> None:
        text = "BTHENUM\\DEV_001122AABBCC"
        self.assertEqual(extract_mac(text), "00:11:22:AA:BB:CC")
        text2 = "BTHENUM\\DEV_001122AABBCC\\7&3B&1&BLUETOOTHDEVICE_001122AABBCC"
        self.assertEqual(extract_mac(text2), "00:11:22:AA:BB:CC")

    def test_extract_mac_ignores_service_uuid_suffix(self) -> None:
        text = "BTHENUM\\{0000110E-0000-1000-8000-00805F9B34FB}_VID&xxxx"
        self.assertEqual(extract_mac(text), "")

    def test_match_target_name_or_mac(self) -> None:
        device = BluetoothDeviceInfo(
            name="Logi MX Mouse",
            instance_id="SIM\\BTH\\001122AABBCC",
            status="OK",
            class_name="Bluetooth",
            present=True,
            mac="00:11:22:AA:BB:CC",
        )
        self.assertTrue(match_target(device, "mx", "", "name_or_mac"))
        self.assertTrue(match_target(device, "", "001122AABBCC", "name_or_mac"))
        self.assertFalse(match_target(device, "abc", "DEADBEEF0000", "name_or_mac"))

    def test_match_target_name_and_mac(self) -> None:
        device = BluetoothDeviceInfo(
            name="MyMouse",
            instance_id="SIM\\BTH\\001122AABBCC",
            status="OK",
            class_name="Bluetooth",
            present=True,
            mac="00:11:22:AA:BB:CC",
        )
        self.assertTrue(match_target(device, "my", "00:11:22:AA:BB:CC", "name_and_mac"))
        self.assertFalse(match_target(device, "other", "00:11:22:AA:BB:CC", "name_and_mac"))

    def test_is_paired_bluetooth_instance(self) -> None:
        self.assertTrue(is_paired_bluetooth_instance("BTHENUM\\DEV_001122AABBCC"))
        self.assertTrue(is_paired_bluetooth_instance("BTHLEDEVICE\\DEV_VID&1234"))
        self.assertFalse(is_paired_bluetooth_instance("USB\\VID_046D&PID_C52B"))
        self.assertFalse(is_paired_bluetooth_instance("HID\\VID_0000&PID_0000"))

    def test_is_primary_device_node(self) -> None:
        self.assertTrue(
            is_primary_device_node("BTHENUM\\DEV_001122AABBCC\\7&3B&1&BLUETOOTHDEVICE_001122AABBCC")
        )
        self.assertTrue(is_primary_device_node("BTHLEDEVICE\\DEV_A1B2C3D4E5F6\\8&1&0"))
        self.assertFalse(
            is_primary_device_node("BTHENUM\\{0000110E-0000-1000-8000-00805F9B34FB}_VID&0001")
        )


if __name__ == "__main__":
    unittest.main()
