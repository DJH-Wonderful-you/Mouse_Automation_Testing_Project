from __future__ import annotations

from dataclasses import dataclass

try:
    import serial.tools.list_ports
except Exception:  # pragma: no cover - import failure handled by fallbacks
    serial = None  # type: ignore[assignment]


@dataclass(slots=True, frozen=True)
class SerialPortInfo:
    device: str
    description: str
    hwid: str

    @property
    def label(self) -> str:
        detail = self.description.strip() or self.hwid.strip()
        if detail:
            return f"{self.device} - {detail}"
        return self.device


def list_serial_ports() -> list[SerialPortInfo]:
    if "serial" not in globals() or serial is None:
        return []
    ports = serial.tools.list_ports.comports()
    return [
        SerialPortInfo(
            device=port.device,
            description=getattr(port, "description", ""),
            hwid=getattr(port, "hwid", ""),
        )
        for port in ports
    ]


def list_serial_device_names() -> list[str]:
    return [port.device for port in list_serial_ports()]
