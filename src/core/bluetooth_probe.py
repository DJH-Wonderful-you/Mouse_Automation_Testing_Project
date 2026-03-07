from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass

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


class BluetoothProbe:
    def query_devices(self) -> list[BluetoothDeviceInfo]:
        return query_bluetooth_devices()

    def is_target_connected(
        self, name_keyword: str, mac: str, mode: BtMatchMode
    ) -> tuple[bool, list[BluetoothDeviceInfo]]:
        return is_target_connected(name_keyword, mac, mode)


def query_bluetooth_devices() -> list[BluetoothDeviceInfo]:
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
        _LOGGER.warning("蓝牙检测返回非JSON内容: %s", output[:300])
        return []

    rows = data if isinstance(data, list) else [data]
    mac_to_hid_signature = _build_ble_hid_service_links(rows)
    hid_signatures = _collect_hid_signatures(rows)
    hid_connected_signatures = _collect_connected_hid_signatures(rows)
    audio_endpoints = _collect_audio_endpoints(rows)
    devices: list[BluetoothDeviceInfo] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
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
        # Prefer entries with explicit class and readable non-empty name.
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
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", payload],
            capture_output=True,
            text=False,
            timeout=timeout_sec,
            check=False,
        )
    except Exception as exc:
        _LOGGER.warning("调用 PowerShell 检测蓝牙失败: %s", exc)
        return ""
    stdout = _decode_powershell_bytes(completed.stdout)
    stderr = _decode_powershell_bytes(completed.stderr)
    if completed.returncode != 0:
        _LOGGER.warning(
            "PowerShell 返回错误(%s): %s",
            completed.returncode,
            (stderr or "").strip(),
        )
    return (stdout or "").strip()


def _decode_powershell_bytes(data: bytes | None) -> str:
    if not data:
        return ""
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "cp936", "utf-16le"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")
