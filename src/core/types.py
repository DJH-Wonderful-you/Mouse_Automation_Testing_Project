from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BtMatchMode = Literal["name_or_mac", "name_and_mac"]


@dataclass(slots=True)
class AppSettings:
    test_count: int = 100
    voltage_threshold_v: float = 3.0
    interval_ms: int = 1000
    relay_channel: int = 1
    multimeter_port: str = ""
    relay_port: str = ""
    bt_name_keyword: str = ""
    bt_mac: str = ""
    bt_match_mode: BtMatchMode = "name_or_mac"
    simulation_multimeter: bool = False
    simulation_relay: bool = False
    simulation_bluetooth: bool = False
    # legacy field: keep for compatibility with old settings payloads.
    simulation_mode: bool = False
    state_timeout_ms: int = 5000
    sample_interval_ms: int = 200
    consecutive_pass_needed: int = 2

    @property
    def any_simulation_enabled(self) -> bool:
        return (
            self.simulation_multimeter
            or self.simulation_relay
            or self.simulation_bluetooth
        )


@dataclass(slots=True)
class VerificationPolicy:
    state_timeout_ms: int = 5000
    sample_interval_ms: int = 200
    consecutive_pass_needed: int = 2


@dataclass(slots=True)
class BluetoothConnectSettings:
    test_count: int = 100
    relay_port: str = ""
    simulation_relay: bool = False
    bt_name_keyword: str = ""
    bt_mac: str = ""
    bt_match_mode: BtMatchMode = "name_or_mac"
    mode_relay_channel: int = 1
    pairing_relay_channel: int = 2
    pairing_press_ms: int = 2000
    state_timeout_ms: int = 15000
    sample_interval_ms: int = 500


@dataclass(slots=True)
class BluetoothSwitchSettings:
    test_count: int = 100
    relay_port: str = ""
    bt_name_keyword: str = ""
    bt_mac: str = ""
    bt_match_mode: BtMatchMode = "name_or_mac"
    mode_relay_channel: int = 1
    pairing_relay_channel: int = 2
    pairing_press_ms: int = 2000
    state_timeout_ms: int = 15000
    sample_interval_ms: int = 500


@dataclass(slots=True)
class CycleResult:
    index: int
    success: bool
    reason: str
    voltage_off: float | None = None
    voltage_on: float | None = None
    bt_off_connected: bool | None = None
    bt_on_connected: bool | None = None


@dataclass(slots=True)
class RunSummary:
    success_count: int
    fail_count: int
    success_rate: float


@dataclass(slots=True)
class BluetoothConnectCycleResult:
    index: int
    success: bool
    reason: str
    paired_before_cycle: bool = False
    removed_before_cycle: bool = False
    paired_after_pairing: bool = False
    connected_after_pairing: bool = False
    removed_after_cycle: bool = False
