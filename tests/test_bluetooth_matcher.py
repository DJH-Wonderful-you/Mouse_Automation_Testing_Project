from __future__ import annotations

import unittest

from src.core.bluetooth_probe import (
    BluetoothDeviceInfo,
    _collect_audio_endpoints,
    _extract_ble_hid_service_link,
    _extract_hid_signature,
    _resolve_audio_endpoint_hint,
    _resolve_connected_hint,
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

    def test_extract_ble_hid_service_link(self) -> None:
        instance_id = (
            "BTHLEDEVICE\\{00001812-0000-1000-8000-00805F9B34FB}_"
            "DEV_VID&02046D_PID&B02C_REV&0014_D5E715414CA8\\8&221C218D&0&001F"
        )
        self.assertEqual(
            _extract_ble_hid_service_link(instance_id),
            ("VID&02046D_PID&B02C_REV&0014", "D5E715414CA8"),
        )
        self.assertIsNone(
            _extract_ble_hid_service_link(
                "BTHLEDEVICE\\{0000180F-0000-1000-8000-00805F9B34FB}_DEV_XXX\\1"
            )
        )

    def test_extract_hid_signature(self) -> None:
        instance_id = (
            "HID\\{00001812-0000-1000-8000-00805F9B34FB}_"
            "DEV_VID&02046D_PID&B02C_REV&0014&COL01\\5&10559097&0&0000"
        )
        self.assertEqual(
            _extract_hid_signature(instance_id), "VID&02046D_PID&B02C_REV&0014"
        )
        self.assertEqual(_extract_hid_signature("USB\\VID_046D&PID_C52B"), "")

    def test_resolve_connected_hint_prefers_hid_state(self) -> None:
        hint = _resolve_connected_hint(
            name="Logi M750",
            status="OK",
            present=True,
            mac="D5:E7:15:41:4C:A8",
            mac_to_hid_signature={"D5E715414CA8": "VID&02046D_PID&B02C_REV&0014"},
            hid_signatures={"VID&02046D_PID&B02C_REV&0014"},
            hid_connected_signatures={"VID&02046D_PID&B02C_REV&0014"},
            audio_endpoints=[],
        )
        self.assertTrue(hint)
        hint2 = _resolve_connected_hint(
            name="Logi M750",
            status="OK",
            present=True,
            mac="D5:E7:15:41:4C:A8",
            mac_to_hid_signature={"D5E715414CA8": "VID&02046D_PID&B02C_REV&0014"},
            hid_signatures={"VID&02046D_PID&B02C_REV&0014"},
            hid_connected_signatures=set(),
            audio_endpoints=[],
        )
        self.assertFalse(hint2)

    def test_resolve_connected_hint_falls_back_without_hid_signature(self) -> None:
        hint = _resolve_connected_hint(
            name="Logi M750",
            status="OK",
            present=True,
            mac="D5:E7:15:41:4C:A8",
            mac_to_hid_signature={"D5E715414CA8": "VID&02046D_PID&B02C_REV&0014"},
            hid_signatures=set(),
            hid_connected_signatures=set(),
            audio_endpoints=[],
        )
        self.assertTrue(hint)

    def test_resolve_connected_hint_hid_priority_over_audio(self) -> None:
        hint = _resolve_connected_hint(
            name="Logi M750",
            status="OK",
            present=True,
            mac="D5:E7:15:41:4C:A8",
            mac_to_hid_signature={"D5E715414CA8": "VID&02046D_PID&B02C_REV&0014"},
            hid_signatures={"VID&02046D_PID&B02C_REV&0014"},
            hid_connected_signatures=set(),
            audio_endpoints=[("耳机 (logi m750)", True)],
        )
        self.assertFalse(hint)

    def test_collect_audio_endpoints(self) -> None:
        rows = [
            {
                "Class": "AudioEndpoint",
                "InstanceId": "SWD\\MMDEVAPI\\{x}",
                "FriendlyName": "耳机 (HUAWEI FreeBuds 5i)",
                "Status": "Unknown",
                "Present": False,
            },
            {
                "Class": "AudioEndpoint",
                "InstanceId": "SWD\\MMDEVAPI\\{y}",
                "FriendlyName": "耳机 (HUAWEI FreeBuds 5i)",
                "Status": "OK",
                "Present": True,
            },
        ]
        self.assertEqual(
            _collect_audio_endpoints(rows),
            [("耳机 (huawei freebuds 5i)", False), ("耳机 (huawei freebuds 5i)", True)],
        )

    def test_resolve_audio_endpoint_hint(self) -> None:
        endpoints = [
            ("耳机 (huawei freebuds 5i)", False),
            ("耳机 (huawei freebuds 5i hands-free)", False),
        ]
        self.assertFalse(_resolve_audio_endpoint_hint("HUAWEI FreeBuds 5i", endpoints))
        endpoints2 = [
            ("耳机 (huawei freebuds 5i)", False),
            ("扬声器 (huawei freebuds 5i)", True),
        ]
        self.assertTrue(_resolve_audio_endpoint_hint("HUAWEI FreeBuds 5i", endpoints2))

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
        self.assertTrue(is_paired_bluetooth_instance("BTHLE\\DEV_D5E715414CA8\\7&1679758C&0&D5E715414CA8"))
        self.assertFalse(is_paired_bluetooth_instance("USB\\VID_046D&PID_C52B"))
        self.assertFalse(is_paired_bluetooth_instance("HID\\VID_0000&PID_0000"))

    def test_is_primary_device_node(self) -> None:
        self.assertTrue(
            is_primary_device_node("BTHENUM\\DEV_001122AABBCC\\7&3B&1&BLUETOOTHDEVICE_001122AABBCC")
        )
        self.assertTrue(is_primary_device_node("BTHLEDEVICE\\DEV_A1B2C3D4E5F6\\8&1&0"))
        self.assertTrue(is_primary_device_node("BTHLE\\DEV_D5E715414CA8\\7&1679758C&0&D5E715414CA8"))
        self.assertFalse(
            is_primary_device_node("BTHENUM\\{0000110E-0000-1000-8000-00805F9B34FB}_VID&0001")
        )


if __name__ == "__main__":
    unittest.main()
