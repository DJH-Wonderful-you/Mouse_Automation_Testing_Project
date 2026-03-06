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


@dataclass(slots=True, frozen=True)
class BluetoothDeviceInfo:
    name: str
    instance_id: str
    status: str
    class_name: str
    present: bool
    mac: str

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
        "($_.InstanceId -match '^(BTHENUM|BTHLEDEVICE)\\\\') };"
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
    connected = any(device.present and device.status_ok for device in matched)
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


def is_paired_bluetooth_instance(instance_id: str) -> bool:
    normalized = (instance_id or "").strip().upper()
    return normalized.startswith("BTHENUM\\") or normalized.startswith("BTHLEDEVICE\\")


def is_primary_device_node(instance_id: str) -> bool:
    normalized = (instance_id or "").strip().upper()
    if normalized.startswith("BTHENUM\\DEV_"):
        return True
    if normalized.startswith("BTHLEDEVICE\\DEV_"):
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


def _run_powershell(script: str, timeout_sec: int = 8) -> str:
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
