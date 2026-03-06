from __future__ import annotations

import logging
import time
from dataclasses import dataclass

try:
    import serial
except Exception:  # pragma: no cover - import failure handled at runtime
    serial = None  # type: ignore[assignment]

_LOGGER = logging.getLogger("multimeter")


@dataclass(slots=True)
class Measurement:
    value: float | str
    function: str
    unit: str
    range_value: float | None
    is_negative: bool
    is_ol: bool
    is_dc: bool
    is_ac: bool
    is_auto: bool


class Victor86EProtocolParser:
    def __init__(self) -> None:
        self.function_codes = {
            0x3B: "V",
            0x3D: "mA",
            0x3F: "mA",
            0x39: "A",
            0xBF: "mA",
            0xB0: "A",
            0xB3: "Ω",
            0xB5: "通断",
            0x31: "二极管",
            0x32: "Hz",
            0xB6: "F",
            0x34: "°",
        }
        self.range_mapping = {
            "V": {
                0x34: 220.00e-3,
                0xB0: 2.2000,
                0x31: 22.000,
                0x32: 220.00,
                0xB3: 1000.0,
            },
            "A": {0x31: 2.2000, 0x32: 22.000, 0xB0: 0.22000},
            "mA": {0xB0: 22.00, 0x31: 220.00, 0x32: 2200.00},
            "Ω": {
                0xB0: 220.00,
                0x31: 2.2000e3,
                0x32: 22.000e3,
                0xB3: 220.00e3,
                0x34: 2.2000e6,
                0xB5: 22.000e6,
                0xB6: 220.00e6,
            },
            "F": {
                0xB0: 22.000e-9,
                0x31: 220.00e-9,
                0x32: 2.2000e-6,
                0x33: 22.000e-6,
                0x34: 220.00e-6,
                0xB5: 2.2000e-3,
                0xB6: 22.000e-3,
                0x37: 220.00e-3,
            },
            "Hz": {
                0xB0: 22.00,
                0x31: 220.0,
                0xB3: 220.00e3,
                0x34: 2.2000e6,
                0xB5: 22.000e6,
                0xB6: 50.00e6,
                0xB7: None,
            },
        }

    def parse(self, data_bytes: bytes) -> Measurement | None:
        if len(data_bytes) != 14:
            return None

        range_byte = data_bytes[0]
        digits = self._parse_digits(data_bytes[1:6])
        if digits is None:
            return None

        function_code = data_bytes[6]
        function = self.function_codes.get(function_code, f"未知(0x{function_code:02X})")

        status_byte = data_bytes[7]
        is_negative = bool(status_byte & 0x04)
        is_ol = bool(status_byte & 0x01)

        dc_ac_auto_byte = data_bytes[10]
        is_dc = bool(dc_ac_auto_byte & 0x08)
        is_ac = bool(dc_ac_auto_byte & 0x04)
        is_auto = bool(dc_ac_auto_byte & 0x02)

        unit = self._get_unit(function, status_byte)
        if is_ol:
            return Measurement(
                value="OL",
                function=function,
                unit=unit,
                range_value=None,
                is_negative=is_negative,
                is_ol=True,
                is_dc=is_dc,
                is_ac=is_ac,
                is_auto=is_auto,
            )

        range_value = self._get_range(function, range_byte)
        value = self._calculate_value(function, digits, range_value)
        if isinstance(value, float) and is_negative:
            value = -value

        return Measurement(
            value=value,
            function=function,
            unit=unit,
            range_value=range_value,
            is_negative=is_negative,
            is_ol=False,
            is_dc=is_dc,
            is_ac=is_ac,
            is_auto=is_auto,
        )

    def _parse_digits(self, digit_bytes: bytes) -> int | None:
        digits: list[str] = []
        for value in digit_bytes:
            if 0xB0 <= value <= 0xB9:
                digits.append(str(value - 0xB0))
            elif 0x30 <= value <= 0x39:
                digits.append(str(value - 0x30))
            elif 0x00 <= value <= 0x09:
                digits.append(str(value))
            else:
                digits.append("0")
        try:
            return int("".join(digits))
        except ValueError:
            return None

    def _get_unit(self, function: str, status_byte: int) -> str:
        if function == "°":
            return "°C" if bool(status_byte & 0x08) else "°F"
        if function == "通断":
            return ""
        if function == "二极管":
            return "V"
        return function

    def _get_range(self, function: str, range_byte: int) -> float | None:
        if function in self.range_mapping and range_byte in self.range_mapping[function]:
            return self.range_mapping[function][range_byte]
        default_ranges = {
            "V": 2.2,
            "A": 0.22000,
            "mA": 220.0,
            "Ω": 220.0,
            "F": 22.0e-9,
            "Hz": 22.0,
            "°": 100.0,
        }
        return default_ranges.get(function)

    def _calculate_value(
        self, function: str, digit_value: int, range_value: float | None
    ) -> float | str:
        if range_value is None:
            if function == "Hz":
                return float(digit_value)
            if function == "°":
                return digit_value / 10.0
            return float(digit_value)

        if function == "V":
            if range_value in {220.00e-3, 2.2000}:
                return digit_value * 0.0001
            if range_value == 22.000:
                return digit_value * 0.001
            if range_value == 220.00:
                return digit_value * 0.01
            if range_value == 1000.0:
                return digit_value * 0.1
            return digit_value * (range_value / 22000.0)

        if function == "A":
            return digit_value * (range_value / 22000.0)
        if function == "mA":
            return digit_value * (range_value / 22000.0)
        if function == "Ω":
            return digit_value * (range_value / 22000.0)
        if function == "F":
            return digit_value * (range_value / 22000.0)
        if function == "Hz":
            return digit_value * (range_value / 22000.0)
        if function == "°":
            return digit_value / 10.0

        return float(digit_value)


class Victor86EMultimeter:
    def __init__(self) -> None:
        self._serial = None
        self._parser = Victor86EProtocolParser()
        self._serial_params = {
            "baudrate": 19200,
            "parity": None if serial is None else serial.PARITY_NONE,
            "stopbits": None if serial is None else serial.STOPBITS_ONE,
            "bytesize": None if serial is None else serial.EIGHTBITS,
            "timeout": 0.6,
        }

    @property
    def is_connected(self) -> bool:
        return bool(self._serial and getattr(self._serial, "is_open", False))

    def connect(self, port: str) -> bool:
        if serial is None:
            raise RuntimeError("pyserial 未安装，无法连接万用表。")
        self.disconnect()
        try:
            self._serial = serial.Serial(  # type: ignore[attr-defined]
                port=port,
                baudrate=self._serial_params["baudrate"],
                parity=self._serial_params["parity"],
                stopbits=self._serial_params["stopbits"],
                bytesize=self._serial_params["bytesize"],
                timeout=self._serial_params["timeout"],
            )
            return True
        except Exception as exc:
            _LOGGER.warning("万用表连接失败(%s): %s", port, exc)
            self._serial = None
            return False

    def disconnect(self) -> None:
        if self._serial and getattr(self._serial, "is_open", False):
            self._serial.close()
        self._serial = None

    def read_measurement(self, attempts: int = 3) -> Measurement | None:
        if not self.is_connected:
            return None

        for _ in range(max(1, attempts)):
            packet = self._read_frame()
            if packet is None:
                continue
            measurement = self._parser.parse(packet)
            if measurement is not None:
                return measurement
        return None

    def read_voltage(self, attempts: int = 3) -> float | None:
        measurement = self.read_measurement(attempts=attempts)
        if measurement is None or measurement.is_ol:
            return None
        if measurement.function != "V":
            return None
        if isinstance(measurement.value, (int, float)):
            return float(measurement.value)
        return None

    def probe_device(self, port: str) -> bool:
        if not self.connect(port):
            return False
        try:
            for _ in range(3):
                measurement = self.read_measurement(attempts=1)
                if measurement is not None:
                    return True
                time.sleep(0.15)
            return False
        finally:
            self.disconnect()

    def _read_frame(self) -> bytes | None:
        if not self.is_connected:
            return None
        try:
            waiting = int(getattr(self._serial, "in_waiting", 0))
            if waiting > 32:
                self._serial.read(waiting - 14)
            frame = self._serial.read(14)
            if len(frame) == 14:
                return frame
            return None
        except Exception as exc:
            _LOGGER.warning("读取万用表数据失败: %s", exc)
            return None
