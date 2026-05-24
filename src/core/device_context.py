from __future__ import annotations

from src.core.bluetooth_pairing import SimulatedBluetoothManager, SystemBluetoothManager
from src.core.bluetooth_probe import BluetoothProbe
from src.core.multimeter_victor86e import Victor86EMultimeter
from src.core.relay_lcus88 import LCUSRelay
from src.core.simulators import (
    SimulatedBluetoothProbe,
    SimulatedMultimeter,
    SimulatedRelay,
)
from src.core.types import DeviceSettings


class DeviceContext:
    """Shared hardware object graph used by device management and test runners."""

    def __init__(self) -> None:
        self._relay_real = LCUSRelay()
        self._multimeter_real = Victor86EMultimeter()
        self._bluetooth_probe_real = BluetoothProbe(
            inventory_cache_ttl_sec=0.0,
            target_cache_ttl_sec=0.0,
        )
        self._bluetooth_manager_real = SystemBluetoothManager(self._bluetooth_probe_real)

        self._relay_sim = SimulatedRelay()
        self._multimeter_sim = SimulatedMultimeter(self._relay_sim)
        self._bluetooth_probe_sim = SimulatedBluetoothProbe(self._relay_sim)
        self._bluetooth_manager_sim = SimulatedBluetoothManager()

    @property
    def relay_real(self) -> LCUSRelay:
        return self._relay_real

    @property
    def multimeter_real(self) -> Victor86EMultimeter:
        return self._multimeter_real

    @property
    def bluetooth_probe_real(self) -> BluetoothProbe:
        return self._bluetooth_probe_real

    @property
    def relay_sim(self) -> SimulatedRelay:
        return self._relay_sim

    @property
    def multimeter_sim(self) -> SimulatedMultimeter:
        return self._multimeter_sim

    @property
    def bluetooth_probe_sim(self) -> SimulatedBluetoothProbe:
        return self._bluetooth_probe_sim

    def active_relay(self, settings: DeviceSettings):
        if settings.simulation_relay:
            self._relay_sim.connect()
            return self._relay_sim
        return self._relay_real

    def active_multimeter(self, settings: DeviceSettings):
        if settings.simulation_multimeter:
            self._multimeter_sim.connect()
            return self._multimeter_sim
        return self._multimeter_real

    def active_bluetooth_probe(self, settings: DeviceSettings):
        if settings.simulation_bluetooth:
            return self._bluetooth_probe_sim
        return self._bluetooth_probe_real

    def active_bluetooth_manager(self, settings: DeviceSettings):
        if settings.simulation_bluetooth:
            return self._bluetooth_manager_sim
        return self._bluetooth_manager_real

    def prepare_power_cycle_simulation(self, relay_channel: int, relay_source) -> None:
        self._multimeter_sim.set_target_channel(relay_channel)
        self._bluetooth_probe_sim.set_target_channel(relay_channel)
        self._multimeter_sim.set_relay_source(relay_source)
        self._bluetooth_probe_sim.set_relay_source(relay_source)

    def relay_ready(self, settings: DeviceSettings) -> bool:
        return settings.simulation_relay or self._relay_real.is_connected

    def multimeter_ready(self, settings: DeviceSettings) -> bool:
        return settings.simulation_multimeter or self._multimeter_real.is_connected

    def shutdown(self) -> None:
        self._relay_real.disconnect()
        self._multimeter_real.disconnect()
