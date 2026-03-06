from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.core.bluetooth_probe import BluetoothDeviceInfo
from src.core.types import BtMatchMode


class SimulatedRelay:
    def __init__(self) -> None:
        self._states = {channel: False for channel in range(1, 9)}
        self.connected = True

    @property
    def is_connected(self) -> bool:
        return self.connected

    def connect(self, port: str = "SIM-RELAY") -> bool:  # noqa: ARG002
        self.connected = True
        return True

    def disconnect(self) -> None:
        self.connected = False

    def query_channel_state(self, channel: int) -> bool:
        self._validate_channel(channel)
        return self._states[channel]

    def set_channel_state(self, channel: int, on: bool) -> None:
        self._validate_channel(channel)
        self._states[channel] = bool(on)

    def query_status(self) -> dict[int, bool]:
        return dict(self._states)

    @staticmethod
    def _validate_channel(channel: int) -> None:
        if channel < 1 or channel > 8:
            raise ValueError("继电器通道范围必须为1-8。")


class RelayStateProvider(Protocol):
    def query_channel_state(self, channel: int) -> bool: ...


class SimulatedMultimeter:
    def __init__(
        self,
        relay: RelayStateProvider,
        target_channel: int = 1,
        voltage_on_v: float = 4.9,
        voltage_off_v: float = 0.2,
    ) -> None:
        self._relay = relay
        self._target_channel = target_channel
        self._voltage_on_v = voltage_on_v
        self._voltage_off_v = voltage_off_v
        self.connected = True

    @property
    def is_connected(self) -> bool:
        return self.connected

    def connect(self, port: str = "SIM-METER") -> bool:  # noqa: ARG002
        self.connected = True
        return True

    def disconnect(self) -> None:
        self.connected = False

    def set_target_channel(self, channel: int) -> None:
        self._target_channel = channel

    def set_relay_source(self, relay: RelayStateProvider) -> None:
        self._relay = relay

    def read_voltage(self, attempts: int = 1) -> float | None:  # noqa: ARG002
        if not self.connected:
            return None
        powered = self._relay.query_channel_state(self._target_channel)
        return self._voltage_on_v if powered else self._voltage_off_v


@dataclass(slots=True)
class _SimTransition:
    stable_state: bool = False
    pending_state: bool = False
    remaining_steps: int = 0


class SimulatedBluetoothProbe:
    def __init__(
        self,
        relay: RelayStateProvider,
        target_channel: int = 1,
        transition_samples: int = 1,
    ) -> None:
        self._relay = relay
        self._target_channel = target_channel
        self._transition_samples = max(0, transition_samples)
        self._transition = _SimTransition()

    def set_target_channel(self, channel: int) -> None:
        self._target_channel = channel

    def query_devices(self) -> list[BluetoothDeviceInfo]:
        connected = self._current_connected_state()
        status = "OK" if connected else "Unknown"
        return [
            BluetoothDeviceInfo(
                name="SimMouse",
                instance_id="SIM\\BTH\\001122AABBCC",
                status=status,
                class_name="Bluetooth",
                present=connected,
                mac="00:11:22:AA:BB:CC",
            )
        ]

    def set_relay_source(self, relay: RelayStateProvider) -> None:
        self._relay = relay

    def is_target_connected(
        self, name_keyword: str, mac: str, mode: BtMatchMode
    ) -> tuple[bool, list[BluetoothDeviceInfo]]:
        connected = self._current_connected_state()
        devices = self.query_devices()
        if not name_keyword and not mac:
            return connected, devices

        from src.core.bluetooth_probe import match_target

        matched = [
            item for item in devices if match_target(item, name_keyword, mac, mode)
        ]
        return connected and bool(matched), matched

    def _current_connected_state(self) -> bool:
        power_state = self._relay.query_channel_state(self._target_channel)
        if power_state != self._transition.pending_state:
            self._transition.pending_state = power_state
            self._transition.remaining_steps = self._transition_samples

        if self._transition.remaining_steps > 0:
            self._transition.remaining_steps -= 1
            return self._transition.stable_state

        self._transition.stable_state = self._transition.pending_state
        return self._transition.stable_state
