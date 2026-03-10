from __future__ import annotations

import logging
import re
import time

try:
    import serial
except Exception:  # pragma: no cover - import failure handled at runtime
    serial = None  # type: ignore[assignment]

_LOGGER = logging.getLogger("relay")


def build_switch_command(channel: int, on: bool) -> bytes:
    if channel < 1 or channel > 8:
        raise ValueError("继电器通道范围必须为1-8。")
    state_byte = 0x01 if on else 0x00
    checksum = (0xA0 + channel + state_byte) & 0xFF
    return bytes([0xA0, channel, state_byte, checksum])


class LCUSRelay:
    def __init__(self) -> None:
        self._serial = None
        self._timeout = 0.5
        self._cached_states: dict[int, bool] = {}
        self._query_supported: bool | None = None

    @property
    def is_connected(self) -> bool:
        return bool(self._serial and getattr(self._serial, "is_open", False))

    def connect(self, port: str) -> bool:
        if serial is None:
            raise RuntimeError("pyserial 未安装，无法连接继电器。")
        self.disconnect()
        try:
            self._serial = serial.Serial(  # type: ignore[attr-defined]
                port=port,
                baudrate=9600,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self._timeout,
            )
            self._cached_states.clear()
            self._query_supported = None
            try:
                self._serial.reset_input_buffer()
            except Exception:
                pass
            time.sleep(0.05)
            return True
        except Exception as exc:
            _LOGGER.warning("继电器连接失败(%s): %s", port, exc)
            self._serial = None
            self._cached_states.clear()
            self._query_supported = None
            return False

    def disconnect(self) -> None:
        if self._serial and getattr(self._serial, "is_open", False):
            self._serial.close()
        self._serial = None
        self._cached_states.clear()
        self._query_supported = None

    def set_channel_state(self, channel: int, on: bool) -> None:
        self._write(build_switch_command(channel, on))
        self._cached_states[channel] = bool(on)
        time.sleep(0.05)

    def query_channel_state(self, channel: int) -> bool:
        try:
            states = self.query_status()
        except Exception:
            if self.is_connected and channel in self._cached_states:
                return self._cached_states[channel]
            raise
        if channel not in states:
            raise RuntimeError("继电器状态返回中未包含目标通道。")
        return states[channel]

    def query_status(self) -> dict[int, bool]:
        if self._query_supported is False and self._cached_states:
            return dict(self._cached_states)

        raw: bytes = b""
        timeouts = (0.2, 0.3)
        for attempt, timeout_s in enumerate(timeouts, start=1):
            self._write(bytes([0xFF]))
            raw = self._read_until_quiet(64, overall_timeout_s=timeout_s)
            states = parse_relay_status(raw)
            if states:
                self._cached_states.update(states)
                self._query_supported = True
                return states
            if raw:
                break
            if attempt < len(timeouts):
                time.sleep(0.05)

        if not raw:
            self._query_supported = False

        hex_dump = raw.hex(" ") if raw else "<empty>"
        raise RuntimeError(f"继电器状态解析失败，原始数据: {hex_dump}")

    def probe_device(self, port: str) -> bool:
        if not self.connect(port):
            return False
        try:
            try:
                self.query_status()
                return True
            except Exception:
                return False
        finally:
            self.disconnect()

    def _write(self, data: bytes) -> None:
        if not self.is_connected:
            raise RuntimeError("继电器未连接。")
        try:
            self._serial.reset_input_buffer()
        except Exception:
            pass
        self._serial.write(data)
        self._serial.flush()

    def _read_all(self, size: int) -> bytes:
        if not self.is_connected:
            raise RuntimeError("继电器未连接。")
        data = self._serial.read(size)
        waiting = int(getattr(self._serial, "in_waiting", 0))
        if waiting > 0:
            data += self._serial.read(waiting)
        return data

    def _read_until_quiet(
        self,
        max_bytes: int,
        overall_timeout_s: float,
        quiet_timeout_s: float = 0.05,
    ) -> bytes:
        if not self.is_connected:
            raise RuntimeError("继电器未连接。")

        deadline = time.time() + max(0.0, overall_timeout_s)
        data = bytearray()
        last_rx = 0.0

        while time.time() < deadline and len(data) < max_bytes:
            waiting = int(getattr(self._serial, "in_waiting", 0))
            if waiting > 0:
                chunk = self._serial.read(min(waiting, max_bytes - len(data)))
                if chunk:
                    data.extend(chunk)
                    last_rx = time.time()
                    continue

            if data and last_rx and (time.time() - last_rx) >= quiet_timeout_s:
                break

            time.sleep(0.01)

        waiting = int(getattr(self._serial, "in_waiting", 0))
        if waiting > 0 and len(data) < max_bytes:
            data.extend(self._serial.read(min(waiting, max_bytes - len(data))))

        return bytes(data)


def parse_relay_status(raw: bytes) -> dict[int, bool]:
    if not raw:
        return {}

    parsed_from_text = _try_parse_from_ascii_hex(raw)
    if parsed_from_text:
        return parsed_from_text

    if len(raw) >= 8:
        first_eight = raw[:8]
        if all(value in (0x00, 0x01) for value in first_eight):
            return {index + 1: bool(value) for index, value in enumerate(first_eight)}
        if all(value in (0x30, 0x31) for value in first_eight):
            return {
                index + 1: (value == 0x31)
                for index, value in enumerate(first_eight)
            }

    if len(raw) >= 1:
        bitmask = raw[0]
        return {index + 1: bool(bitmask & (1 << index)) for index in range(8)}

    return {}


def _try_parse_from_ascii_hex(raw: bytes) -> dict[int, bool]:
    text = raw.decode("ascii", errors="ignore")
    pairs = re.findall(r"\b[0-9A-Fa-f]{2}\b", text)
    if len(pairs) < 8:
        return {}
    values = [int(pair, 16) for pair in pairs[:8]]
    if not all(value in (0x00, 0x01) for value in values):
        return {}
    return {index + 1: bool(value) for index, value in enumerate(values)}
