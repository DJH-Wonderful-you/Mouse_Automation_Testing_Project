from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace

from src.core.types import BtMatchMode

_LOGGER = logging.getLogger("bluetooth")
_MAC_WITH_SEP_PATTERN = re.compile(r"(?:[0-9A-Fa-f]{2}[-:]){5}[0-9A-Fa-f]{2}")
_INSTANCE_MAC_PATTERNS = [
    re.compile(r"BLUETOOTHDEVICE_([0-9A-Fa-f]{12})", re.IGNORECASE),
    re.compile(r"(?:^|\\|_)DEV_([0-9A-Fa-f]{12})(?:\\|_|$)", re.IGNORECASE),
]
_BLE_HID_SERVICE_LINK_PATTERN = re.compile(
    r"^BTHLEDEVICE\\\{00001812-0000-1000-8000-00805F9B34FB\}_DEV_"
    r"(VID&[0-9A-F]{4,6}_PID&[0-9A-F]{4}(?:_REV&[0-9A-F]{4})?)_([0-9A-F]{12})\\",
    re.IGNORECASE,
)
_HID_BLE_SIGNATURE_PATTERN = re.compile(
    r"^HID\\\{00001812-0000-1000-8000-00805F9B34FB\}_DEV_"
    r"(VID&[0-9A-F]{4,6}_PID&[0-9A-F]{4}(?:_REV&[0-9A-F]{4})?)",
    re.IGNORECASE,
)
_BT_INSTANCE_PREFIXES = ("BTHENUM\\", "BTHLEDEVICE\\", "BTHLE\\")
_BT_PRIMARY_PREFIXES = ("BTHENUM\\DEV_", "BTHLEDEVICE\\DEV_", "BTHLE\\DEV_")
_PNPUTIL_CLASS_NAMES = ("Bluetooth", "HIDClass", "AudioEndpoint")
_PNPUTIL_STATUS_OK = "OK"
_PNPUTIL_PRESENT_FALSE_STATUSES = {"DISCONNECTED", "UNKNOWN", "NOT PRESENT"}
_PNPUTIL_ENUM_FORMAT_PREFERENCE = "csv"


@dataclass(slots=True, frozen=True)
class BluetoothDeviceInfo:
    name: str
    instance_id: str
    status: str
    class_name: str
    present: bool
    mac: str
    connected: bool | None = None

    @property
    def status_ok(self) -> bool:
        return self.status.strip().upper() == "OK"

    @property
    def summary(self) -> str:
        mac_part = self.mac if self.mac else "-"
        return (
            f"名称={self.name or '-'} | 状态={self.status or '-'} | "
            f"MAC={mac_part} | Class={self.class_name or '-'} | ID={self.instance_id or '-'}"
        )


@dataclass(slots=True)
class _BluetoothInventory:
    rows: list[dict[str, object]]
    devices: list[BluetoothDeviceInfo]
    created_at: float


@dataclass(slots=True)
class _TrackedDevice:
    device: BluetoothDeviceInfo
    watcher_ids: tuple[str, ...]
    prefer_hid_watchers: bool


@dataclass(slots=True)
class _TargetCacheEntry:
    key: tuple[str, str, BtMatchMode]
    tracked_devices: tuple[_TrackedDevice, ...]
    created_at: float


class BluetoothProbe:
    def __init__(
        self,
        *,
        inventory_cache_ttl_sec: float = 1.0,
        target_cache_ttl_sec: float = 300.0,
    ) -> None:
        self._inventory_cache_ttl_sec = max(0.0, inventory_cache_ttl_sec)
        self._target_cache_ttl_sec = max(self._inventory_cache_ttl_sec, target_cache_ttl_sec)
        self._inventory_cache: _BluetoothInventory | None = None
        self._target_cache: dict[tuple[str, str, BtMatchMode], _TargetCacheEntry] = {}

    def query_devices(self) -> list[BluetoothDeviceInfo]:
        inventory = self._get_inventory()
        return list(inventory.devices)

    def is_target_connected(
        self, name_keyword: str, mac: str, mode: BtMatchMode
    ) -> tuple[bool, list[BluetoothDeviceInfo]]:
        key = _make_target_cache_key(name_keyword, mac, mode)
        entry = self._target_cache.get(key)
        now = time.monotonic()
        if entry is not None and now - entry.created_at <= self._target_cache_ttl_sec:
            fast_result = self._resolve_target_connection_fast(entry)
            if fast_result is not None:
                entry.created_at = now
                return fast_result

        entry, matched = self._rebuild_target_cache_entry(key)
        if entry is not None:
            self._target_cache[key] = entry
        else:
            self._target_cache.pop(key, None)
        connected = any(_is_device_connected(device) for device in matched)
        return connected, matched

    def _get_inventory(self, *, force_refresh: bool = False) -> _BluetoothInventory:
        now = time.monotonic()
        if (
            not force_refresh
            and self._inventory_cache is not None
            and now - self._inventory_cache.created_at <= self._inventory_cache_ttl_sec
        ):
            return self._inventory_cache
        inventory = _query_bluetooth_inventory()
        self._inventory_cache = inventory
        return inventory

    def _rebuild_target_cache_entry(
        self, key: tuple[str, str, BtMatchMode]
    ) -> tuple[_TargetCacheEntry | None, list[BluetoothDeviceInfo]]:
        inventory = self._get_inventory()
        name_keyword, mac, mode = key
        matched = [
            device
            for device in inventory.devices
            if match_target(device, name_keyword=name_keyword, mac=mac, mode=mode)
        ]
        if not matched:
            return None, []
        tracked_devices = tuple(_build_tracked_devices(inventory.rows, matched))
        entry = _TargetCacheEntry(
            key=key,
            tracked_devices=tracked_devices,
            created_at=time.monotonic(),
        )
        return entry, matched

    def _resolve_target_connection_fast(
        self, entry: _TargetCacheEntry
    ) -> tuple[bool, list[BluetoothDeviceInfo]] | None:
        if not entry.tracked_devices:
            return None

        rows_by_instance_id: dict[str, dict[str, object]] = {}
        for tracked_device in entry.tracked_devices:
            if not tracked_device.watcher_ids:
                return None
            for watcher_id in tracked_device.watcher_ids:
                if watcher_id in rows_by_instance_id:
                    continue
                row = _query_pnputil_instance_row(watcher_id)
                if row is None:
                    return None
                rows_by_instance_id[watcher_id] = row

        updated_devices: list[BluetoothDeviceInfo] = []
        for tracked_device in entry.tracked_devices:
            connected = _resolve_tracked_device_connection(tracked_device, rows_by_instance_id)
            if connected is None:
                return None
            updated_devices.append(replace(tracked_device.device, connected=connected))

        connected = any(_is_device_connected(device) for device in updated_devices)
        return connected, updated_devices


def query_bluetooth_devices() -> list[BluetoothDeviceInfo]:
    inventory = _query_bluetooth_inventory()
    return inventory.devices


def is_target_connected(
    name_keyword: str, mac: str, mode: BtMatchMode
) -> tuple[bool, list[BluetoothDeviceInfo]]:
    devices = query_bluetooth_devices()
    matched = [
        device
        for device in devices
        if match_target(device, name_keyword=name_keyword, mac=mac, mode=mode)
    ]
    connected = any(_is_device_connected(device) for device in matched)
    return connected, matched


def match_target(
    device: BluetoothDeviceInfo, name_keyword: str, mac: str, mode: BtMatchMode
) -> bool:
    keyword = (name_keyword or "").strip().lower()
    target_mac = normalize_mac(mac)
    device_name = device.name.lower()
    name_match = bool(keyword) and keyword in device_name
    mac_match = bool(target_mac) and target_mac == normalize_mac(device.mac)

    if mode == "name_and_mac":
        if keyword and target_mac:
            return name_match and mac_match
        if keyword:
            return name_match
        if target_mac:
            return mac_match
        return False

    if keyword and target_mac:
        return name_match or mac_match
    if keyword:
        return name_match
    if target_mac:
        return mac_match
    return False


def normalize_mac(mac: str) -> str:
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", mac or "").upper()
    if len(cleaned) != 12:
        return ""
    return ":".join(cleaned[index : index + 2] for index in range(0, 12, 2))


def extract_mac(text: str) -> str:
    payload = text or ""
    for pattern in _INSTANCE_MAC_PATTERNS:
        match = pattern.search(payload)
        if match:
            return normalize_mac(match.group(1))
    with_sep = _MAC_WITH_SEP_PATTERN.search(payload)
    if with_sep:
        return normalize_mac(with_sep.group(0))
    return ""


def _looks_like_mouse_related(name: str, class_name: str, instance_id: str) -> bool:
    text = f"{name} {class_name} {instance_id}".lower()
    keywords = ("mouse", "鼠标", "bluetooth", "蓝牙", "hid", "bth")
    return any(keyword in text for keyword in keywords)


def _query_bluetooth_inventory() -> _BluetoothInventory:
    rows = _query_bluetooth_rows()
    devices = _build_devices_from_rows(rows)
    return _BluetoothInventory(rows=rows, devices=devices, created_at=time.monotonic())


def _query_bluetooth_rows() -> list[dict[str, object]]:
    pnputil_rows = _query_bluetooth_rows_via_pnputil()
    if pnputil_rows is not None:
        return pnputil_rows
    return _query_bluetooth_rows_via_powershell()


def _query_bluetooth_rows_via_pnputil() -> list[dict[str, object]] | None:
    rows: list[dict[str, object]] = []
    for class_name in _PNPUTIL_CLASS_NAMES:
        class_rows = _run_pnputil_rows(
            ["pnputil", "/enum-devices", "/class", class_name],
            timeout_sec=10,
        )
        if class_rows is None:
            return None
        rows.extend(class_rows)
    return rows


def _query_pnputil_instance_row(instance_id: str) -> dict[str, object] | None:
    rows = _run_pnputil_rows(
        ["pnputil", "/enum-devices", "/instanceid", instance_id],
        timeout_sec=5,
    )
    if rows is None or not rows:
        return None
    return rows[0]


def _run_pnputil_rows(
    base_args: list[str], *, timeout_sec: int
) -> list[dict[str, object]] | None:
    """Run pnputil /enum-devices and return normalized rows.

    Different Windows builds ship different pnputil capabilities. Some Win10 builds
    do not support `/format csv` (they print usage to stdout and return exit code 1),
    so we try multiple formats and remember the first one that works to avoid repeated
    failures during fast connection polling.
    """

    global _PNPUTIL_ENUM_FORMAT_PREFERENCE  # noqa: PLW0603

    preference = _PNPUTIL_ENUM_FORMAT_PREFERENCE
    candidates = [preference, "csv", "xml", "txt"]
    formats_to_try: list[str] = []
    for fmt in candidates:
        if fmt not in formats_to_try:
            formats_to_try.append(fmt)

    for fmt in formats_to_try:
        if fmt == "csv":
            rows = _run_pnputil_csv(
                [*base_args, "/format", "csv"],
                timeout_sec=timeout_sec,
            )
        elif fmt == "xml":
            rows = _run_pnputil_xml(
                [*base_args, "/format", "xml"],
                timeout_sec=timeout_sec,
            )
        else:
            rows = _run_pnputil_text(
                base_args,
                timeout_sec=timeout_sec,
            )

        if rows is None:
            continue

        _PNPUTIL_ENUM_FORMAT_PREFERENCE = fmt
        return rows

    return None


def _run_pnputil_csv(
    args: list[str], *, timeout_sec: int
) -> list[dict[str, object]] | None:
    completed = _run_process(args, timeout_sec=timeout_sec)
    if completed is None:
        return None
    if completed.returncode != 0:
        return None
    output = _decode_process_bytes(completed.stdout)
    try:
        return _parse_pnputil_csv_output(output)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("failed to parse pnputil output: %s", exc)
        return None


def _parse_pnputil_csv_output(output: str) -> list[dict[str, object]]:
    payload = (output or "").strip()
    if not payload:
        return []
    reader = csv.DictReader(io.StringIO(payload.lstrip("\ufeff")))
    rows: list[dict[str, object]] = []
    for raw_row in reader:
        normalized_row = _normalize_pnputil_row(raw_row)
        if normalized_row is not None:
            rows.append(normalized_row)
    return rows


def _run_pnputil_xml(
    args: list[str], *, timeout_sec: int
) -> list[dict[str, object]] | None:
    completed = _run_process(args, timeout_sec=timeout_sec)
    if completed is None:
        return None
    if completed.returncode != 0:
        return None
    output = _decode_process_bytes(completed.stdout)
    try:
        return _parse_pnputil_xml_output(output)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("failed to parse pnputil XML output: %s", exc)
        return None


def _parse_pnputil_xml_output(output: str) -> list[dict[str, object]]:
    payload = (output or "").strip()
    if not payload:
        return []

    root = ET.fromstring(payload.lstrip("\ufeff"))
    rows: list[dict[str, object]] = []
    for device_node in root.findall(".//Device"):
        instance_id = str(device_node.get("InstanceId") or "").strip()
        if not instance_id:
            continue
        status = _normalize_pnputil_status(str(device_node.findtext("Status") or ""))
        rows.append(
            {
                "Status": status,
                "Class": str(device_node.findtext("ClassName") or "").strip(),
                "FriendlyName": str(device_node.findtext("DeviceDescription") or "").strip(),
                "InstanceId": instance_id,
                "Present": _pnputil_status_is_present(status),
            }
        )
    return rows


_PNPUTIL_TEXT_KEYS = {
    # English (current pnputil output on en-US builds).
    "instance id": "instance_id",
    "device description": "friendly_name",
    "class name": "class_name",
    "status": "status",
    # Chinese (common translations; keep best-effort to support zh-CN builds).
    "实例 id": "instance_id",
    "实例id": "instance_id",
    "设备说明": "friendly_name",
    "设备描述": "friendly_name",
    "类名": "class_name",
    "状态": "status",
}


def _run_pnputil_text(
    args: list[str], *, timeout_sec: int
) -> list[dict[str, object]] | None:
    completed = _run_process(args, timeout_sec=timeout_sec)
    if completed is None:
        return None
    if completed.returncode != 0:
        return None
    output = _decode_process_bytes(completed.stdout)
    try:
        return _parse_pnputil_text_output(output)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("failed to parse pnputil text output: %s", exc)
        return None


def _parse_pnputil_text_output(output: str) -> list[dict[str, object]]:
    payload = (output or "").replace("\r\n", "\n").strip()
    if not payload:
        return []

    blocks = re.split(r"\n\s*\n+", payload)
    rows: list[dict[str, object]] = []
    for block in blocks:
        instance_id = ""
        friendly_name = ""
        class_name = ""
        status = ""
        for line in block.splitlines():
            match = re.match(r"^\s*([^:：]+?)\s*[:：]\s*(.*?)\s*$", line)
            if not match:
                continue
            raw_key = re.sub(r"\s+", " ", match.group(1).strip().lower())
            value = match.group(2).strip()
            canonical = _PNPUTIL_TEXT_KEYS.get(raw_key)
            if canonical == "instance_id":
                instance_id = value
            elif canonical == "friendly_name":
                friendly_name = value
            elif canonical == "class_name":
                class_name = value
            elif canonical == "status":
                status = value

        instance_id = instance_id.strip()
        if not instance_id:
            continue

        normalized_status = _normalize_pnputil_status(status)
        rows.append(
            {
                "Status": normalized_status,
                "Class": (class_name or "").strip(),
                "FriendlyName": (friendly_name or "").strip(),
                "InstanceId": instance_id,
                "Present": _pnputil_status_is_present(normalized_status),
            }
        )
    return rows


def _normalize_pnputil_row(raw_row: dict[str, str | None]) -> dict[str, object] | None:
    instance_id = str(raw_row.get("InstanceId") or "").strip()
    if not instance_id:
        return None
    status = _normalize_pnputil_status(str(raw_row.get("Status") or ""))
    return {
        "Status": status,
        "Class": str(raw_row.get("ClassName") or "").strip(),
        "FriendlyName": str(raw_row.get("DeviceDescription") or "").strip(),
        "InstanceId": instance_id,
        "Present": _pnputil_status_is_present(status),
    }


def _normalize_pnputil_status(status: str) -> str:
    normalized = (status or "").strip()
    if normalized.upper() == "STARTED":
        return _PNPUTIL_STATUS_OK
    return normalized


def _pnputil_status_is_present(status: str) -> bool:
    normalized = (status or "").strip().upper()
    if not normalized:
        return False
    return normalized not in _PNPUTIL_PRESENT_FALSE_STATUSES


def _query_bluetooth_rows_via_powershell() -> list[dict[str, object]]:
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$items = Get-PnpDevice | Where-Object { "
        "($_.InstanceId -match '^(BTHENUM|BTHLEDEVICE|BTHLE)\\\\' "
        "-or $_.InstanceId -match '^HID\\\\\\{00001812-0000-1000-8000-00805F9B34FB\\}_DEV_' "
        "-or ($_.InstanceId -match '^SWD\\\\MMDEVAPI\\\\' -and $_.Class -eq 'AudioEndpoint')) };"
        "$items | Select-Object Status,Class,FriendlyName,InstanceId,Present | "
        "ConvertTo-Json -Compress -Depth 3"
    )
    output = _run_powershell(script)
    if not output:
        return []

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        _LOGGER.warning("bluetooth probe returned non-JSON content: %s", output[:300])
        return []

    rows = data if isinstance(data, list) else [data]
    return [row for row in rows if isinstance(row, dict)]


def _build_devices_from_rows(rows: list[dict[str, object]]) -> list[BluetoothDeviceInfo]:
    mac_to_hid_signature = _build_ble_hid_service_links(rows)
    hid_signatures = _collect_hid_signatures(rows)
    hid_connected_signatures = _collect_connected_hid_signatures(rows)
    audio_endpoints = _collect_audio_endpoints(rows)
    devices: list[BluetoothDeviceInfo] = []
    for row in rows:
        name = str(row.get("FriendlyName") or "")
        instance_id = str(row.get("InstanceId") or "")
        class_name = str(row.get("Class") or "")
        status = str(row.get("Status") or "")
        present = bool(row.get("Present", True))
        if not is_paired_bluetooth_instance(instance_id):
            continue
        if not is_primary_device_node(instance_id):
            continue
        merged_text = f"{name} {instance_id}"
        mac = extract_mac(merged_text)
        connected = _resolve_connected_hint(
            name=name,
            status=status,
            present=present,
            mac=mac,
            mac_to_hid_signature=mac_to_hid_signature,
            hid_signatures=hid_signatures,
            hid_connected_signatures=hid_connected_signatures,
            audio_endpoints=audio_endpoints,
        )
        if not _looks_like_mouse_related(name, class_name, instance_id):
            continue
        devices.append(
            BluetoothDeviceInfo(
                name=name,
                instance_id=instance_id,
                status=status,
                class_name=class_name,
                present=present,
                mac=mac,
                connected=connected,
            )
        )
    return _deduplicate_devices(devices)


def _build_ble_hid_service_links(rows: list[object]) -> dict[str, str]:
    links: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        instance_id = str(row.get("InstanceId") or "")
        parsed = _extract_ble_hid_service_link(instance_id)
        if not parsed:
            continue
        signature, mac_key = parsed
        links[mac_key] = signature
    return links


def _collect_connected_hid_signatures(rows: list[object]) -> set[str]:
    connected_signatures: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        instance_id = str(row.get("InstanceId") or "")
        signature = _extract_hid_signature(instance_id)
        if not signature:
            continue
        status = str(row.get("Status") or "")
        present = bool(row.get("Present", True))
        if present and status.strip().upper() == "OK":
            connected_signatures.add(signature)
    return connected_signatures


def _collect_hid_signatures(rows: list[object]) -> set[str]:
    signatures: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        instance_id = str(row.get("InstanceId") or "")
        signature = _extract_hid_signature(instance_id)
        if signature:
            signatures.add(signature)
    return signatures


def _collect_hid_instance_ids_by_signature(rows: list[object]) -> dict[str, set[str]]:
    instance_ids_by_signature: dict[str, set[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        instance_id = str(row.get("InstanceId") or "")
        signature = _extract_hid_signature(instance_id)
        if not signature:
            continue
        instance_ids_by_signature.setdefault(signature, set()).add(instance_id)
    return instance_ids_by_signature


def _collect_audio_endpoints(rows: list[object]) -> list[tuple[str, bool]]:
    endpoints: list[tuple[str, bool]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        class_name = str(row.get("Class") or "")
        instance_id = str(row.get("InstanceId") or "")
        if class_name.strip().lower() != "audioendpoint" and not instance_id.upper().startswith(
            "SWD\\MMDEVAPI\\"
        ):
            continue
        name = str(row.get("FriendlyName") or "").strip().lower()
        if not name:
            continue
        status = str(row.get("Status") or "")
        present = bool(row.get("Present", True))
        endpoints.append((name, present and status.strip().upper() == "OK"))
    return endpoints


def _build_tracked_devices(
    rows: list[dict[str, object]], matched_devices: list[BluetoothDeviceInfo]
) -> list[_TrackedDevice]:
    mac_to_hid_signature = _build_ble_hid_service_links(rows)
    hid_instance_ids_by_signature = _collect_hid_instance_ids_by_signature(rows)
    tracked_devices: list[_TrackedDevice] = []
    for device in matched_devices:
        watcher_ids: list[str] = []
        prefer_hid_watchers = False
        mac_key = normalize_mac(device.mac).replace(":", "")
        signature = mac_to_hid_signature.get(mac_key)
        if signature:
            hid_instance_ids = sorted(hid_instance_ids_by_signature.get(signature, set()))
            if hid_instance_ids:
                watcher_ids.extend(hid_instance_ids)
                prefer_hid_watchers = True
        if not watcher_ids and device.instance_id:
            watcher_ids.append(device.instance_id)
        tracked_devices.append(
            _TrackedDevice(
                device=device,
                watcher_ids=tuple(dict.fromkeys(watcher_ids)),
                prefer_hid_watchers=prefer_hid_watchers,
            )
        )
    return tracked_devices


def _resolve_tracked_device_connection(
    tracked_device: _TrackedDevice,
    rows_by_instance_id: dict[str, dict[str, object]],
) -> bool | None:
    watcher_rows: list[dict[str, object]] = []
    for watcher_id in tracked_device.watcher_ids:
        row = rows_by_instance_id.get(watcher_id)
        if row is None:
            return None
        watcher_rows.append(row)
    if not watcher_rows:
        return None
    if tracked_device.prefer_hid_watchers:
        return any(_row_indicates_connected(row) for row in watcher_rows)
    return _row_indicates_connected(watcher_rows[0])


def _row_indicates_connected(row: dict[str, object]) -> bool:
    status = str(row.get("Status") or "")
    present = bool(row.get("Present", True))
    return present and status.strip().upper() == "OK"


def _make_target_cache_key(
    name_keyword: str, mac: str, mode: BtMatchMode
) -> tuple[str, str, BtMatchMode]:
    normalized_mode: BtMatchMode = "name_and_mac" if mode == "name_and_mac" else "name_or_mac"
    return ((name_keyword or "").strip().lower(), normalize_mac(mac), normalized_mode)


def _resolve_connected_hint(
    *,
    name: str,
    status: str,
    present: bool,
    mac: str,
    mac_to_hid_signature: dict[str, str],
    hid_signatures: set[str],
    hid_connected_signatures: set[str],
    audio_endpoints: list[tuple[str, bool]],
) -> bool | None:
    mac_key = normalize_mac(mac).replace(":", "")
    signature = mac_to_hid_signature.get(mac_key)
    if signature and signature in hid_signatures:
        return signature in hid_connected_signatures
    audio_hint = _resolve_audio_endpoint_hint(name, audio_endpoints)
    if audio_hint is not None:
        return audio_hint
    if status or present:
        return present and status.strip().upper() == "OK"
    return None


def _resolve_audio_endpoint_hint(
    device_name: str, audio_endpoints: list[tuple[str, bool]]
) -> bool | None:
    normalized_name = (device_name or "").strip().lower()
    if not normalized_name or not audio_endpoints:
        return None

    matches = [connected for endpoint_name, connected in audio_endpoints if normalized_name in endpoint_name]
    if not matches:
        return None
    return any(matches)


def _extract_ble_hid_service_link(instance_id: str) -> tuple[str, str] | None:
    match = _BLE_HID_SERVICE_LINK_PATTERN.match((instance_id or "").strip())
    if not match:
        return None
    signature = match.group(1).upper()
    mac_key = match.group(2).upper()
    return signature, mac_key


def _extract_hid_signature(instance_id: str) -> str:
    match = _HID_BLE_SIGNATURE_PATTERN.match((instance_id or "").strip())
    if not match:
        return ""
    return match.group(1).upper()


def _is_device_connected(device: BluetoothDeviceInfo) -> bool:
    if device.connected is not None:
        return bool(device.connected)
    return device.present and device.status_ok


def is_paired_bluetooth_instance(instance_id: str) -> bool:
    normalized = (instance_id or "").strip().upper()
    return normalized.startswith(_BT_INSTANCE_PREFIXES)


def is_primary_device_node(instance_id: str) -> bool:
    normalized = (instance_id or "").strip().upper()
    if normalized.startswith(_BT_PRIMARY_PREFIXES):
        return True
    return "BLUETOOTHDEVICE_" in normalized


def _deduplicate_devices(devices: list[BluetoothDeviceInfo]) -> list[BluetoothDeviceInfo]:
    if not devices:
        return []
    result: dict[str, BluetoothDeviceInfo] = {}
    for device in devices:
        key = device.mac or (device.instance_id or "").upper()
        if not key:
            continue
        if key not in result:
            result[key] = device
            continue
        existing = result[key]
        existing_score = int(bool(existing.name.strip())) + int(bool(existing.class_name.strip()))
        current_score = int(bool(device.name.strip())) + int(bool(device.class_name.strip()))
        if current_score > existing_score:
            result[key] = device
    return list(result.values())


def _run_powershell(script: str, timeout_sec: int = 15) -> str:
    payload = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "$OutputEncoding=[System.Text.Encoding]::UTF8;"
        + script
    )
    completed = _run_process(
        ["powershell", "-NoProfile", "-Command", payload],
        timeout_sec=timeout_sec,
    )
    if completed is None:
        return ""
    return (_decode_process_bytes(completed.stdout) or "").strip()


def _run_process(args: list[str], *, timeout_sec: int) -> subprocess.CompletedProcess[bytes] | None:
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=False,
            timeout=timeout_sec,
            check=False,
            **_build_hidden_subprocess_kwargs(),
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("failed to run command %s: %s", args[0], exc)
        return None
    stderr = _decode_process_bytes(completed.stderr)
    if completed.returncode != 0:
        stdout = _decode_process_bytes(completed.stdout)
        detail = (stderr or "").strip() or (stdout or "").strip()
        detail = detail[:300] if detail else ""
        _LOGGER.warning(
            "command failed (%s) %s: %s",
            completed.returncode,
            args[0],
            detail,
        )
    return completed


def _decode_process_bytes(data: bytes | None) -> str:
    if not data:
        return ""
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "cp936", "utf-16le"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _build_hidden_subprocess_kwargs() -> dict[str, object]:
    if os.name != "nt":
        return {}

    kwargs: dict[str, object] = {}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if creationflags:
        kwargs["creationflags"] = creationflags

    startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_cls is None:
        return kwargs

    startupinfo = startupinfo_cls()
    use_show_window = getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
    if use_show_window:
        startupinfo.dwFlags |= use_show_window
    startupinfo.wShowWindow = 0
    kwargs["startupinfo"] = startupinfo
    return kwargs
