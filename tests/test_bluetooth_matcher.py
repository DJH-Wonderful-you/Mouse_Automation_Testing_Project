from __future__ import annotations

import unittest
from unittest.mock import patch

from src.core.bluetooth_probe import (
    BluetoothProbe,
    BluetoothDeviceInfo,
    _BluetoothInventory,
    _collect_audio_endpoints,
    _collect_hid_instance_ids_by_signature,
    _extract_ble_hid_service_link,
    _extract_hid_signature,
    _parse_pnputil_csv_output,
    _resolve_audio_endpoint_hint,
    _resolve_connected_hint,
    extract_mac,
    is_paired_bluetooth_instance,
    is_primary_device_node,
    match_target,
    normalize_mac,
)


class TestBluetoothMatcher(unittest.TestCase):
    def test_parse_pnputil_csv_output(self) -> None:
        output = "\n".join(
            [
                "InstanceId,DeviceDescription,ClassName,Status",
                '"BTHLE\\Dev_d5e715414cac\\7&1679758c&0&d5e715414cac","Logi M750","Bluetooth","Started"',
                '"HID\\{00001812-0000-1000-8000-00805f9b34fb}_Dev_VID&02046d_PID&b02c_REV&0014&Col02\\9&3a398936&0&0001","Logitech Download Assistant","HIDClass","Disconnected"',
            ]
        )
        rows = _parse_pnputil_csv_output(output)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["Status"], "OK")
        self.assertTrue(rows[0]["Present"])
        self.assertEqual(rows[1]["Status"], "Disconnected")
        self.assertFalse(rows[1]["Present"])

    def test_collect_hid_instance_ids_by_signature(self) -> None:
        rows = [
            {
                "InstanceId": (
                    "HID\\{00001812-0000-1000-8000-00805F9B34FB}_"
                    "DEV_VID&02046D_PID&B02C_REV&0014&COL02\\9&3A398936&0&0001"
                )
            },
            {
                "InstanceId": (
                    "HID\\{00001812-0000-1000-8000-00805F9B34FB}_"
                    "DEV_VID&02046D_PID&B02C_REV&0014&COL03\\9&3A398936&0&0002"
                )
            },
        ]
        self.assertEqual(
            _collect_hid_instance_ids_by_signature(rows),
            {
                "VID&02046D_PID&B02C_REV&0014": {
                    rows[0]["InstanceId"],
                    rows[1]["InstanceId"],
                }
            },
        )

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

    def test_bluetooth_probe_reuses_cached_watchers_for_fast_status_check(self) -> None:
        device = BluetoothDeviceInfo(
            name="Logi M750",
            instance_id="BTHLE\\DEV_D5E715414CAC\\7&1679758C&0&D5E715414CAC",
            status="OK",
            class_name="Bluetooth",
            present=True,
            mac="D5:E7:15:41:4C:AC",
            connected=False,
        )
        rows = [
            {
                "InstanceId": device.instance_id,
                "FriendlyName": device.name,
                "Class": "Bluetooth",
                "Status": "OK",
                "Present": True,
            },
            {
                "InstanceId": (
                    "BTHLEDEVICE\\{00001812-0000-1000-8000-00805F9B34FB}_"
                    "DEV_VID&02046D_PID&B02C_REV&0014_D5E715414CAC\\8&2E2996DB&0&001F"
                ),
                "FriendlyName": "Bluetooth Low Energy GATT compliant HID device",
                "Class": "HIDClass",
                "Status": "OK",
                "Present": True,
            },
            {
                "InstanceId": (
                    "HID\\{00001812-0000-1000-8000-00805F9B34FB}_"
                    "DEV_VID&02046D_PID&B02C_REV&0014&COL02\\9&3A398936&0&0001"
                ),
                "FriendlyName": "Logitech Download Assistant",
                "Class": "HIDClass",
                "Status": "Disconnected",
                "Present": False,
            },
        ]
        inventory = _BluetoothInventory(rows=rows, devices=[device], created_at=0.0)
        probe = BluetoothProbe(inventory_cache_ttl_sec=60.0, target_cache_ttl_sec=60.0)
        hid_instance_id = rows[2]["InstanceId"]

        def _fake_run_pnputil_csv(args: list[str], *, timeout_sec: int) -> list[dict[str, object]]:
            self.assertIn("/instanceid", args)
            instance_id = args[args.index("/instanceid") + 1]
            self.assertEqual(instance_id, hid_instance_id)
            return [
                {
                    "InstanceId": instance_id,
                    "FriendlyName": "Logitech Download Assistant",
                    "Class": "HIDClass",
                    "Status": "OK",
                    "Present": True,
                }
            ]

        with (
            patch("src.core.bluetooth_probe._query_bluetooth_inventory", return_value=inventory) as mock_query,
            patch("src.core.bluetooth_probe._run_pnputil_csv", side_effect=_fake_run_pnputil_csv),
        ):
            connected_first, matched_first = probe.is_target_connected("logi", "", "name_or_mac")
            connected_second, matched_second = probe.is_target_connected("logi", "", "name_or_mac")

        self.assertFalse(connected_first)
        self.assertFalse(matched_first[0].connected)
        self.assertTrue(connected_second)
        self.assertTrue(matched_second[0].connected)
        self.assertEqual(mock_query.call_count, 1)


if __name__ == "__main__":
    unittest.main()
