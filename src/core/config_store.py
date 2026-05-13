from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSettings

from src.core.types import (
    TEST_ITEM_ORDER,
    AppSettings,
    BluetoothConnectSettings,
    BluetoothSwitchMethod,
    BluetoothSwitchSettings,
    BluetoothTargetSettings,
    DeviceSettings,
    SuiteMode,
    TestItemId,
    TestPlanSettings,
)

_ORG_NAME = "RJHZ"
_APP_NAME = "MouseAutomationTool"
_GROUP_POWER_CYCLE = "power_cycle"
_GROUP_BLUETOOTH_CONNECT = "bluetooth_connect"
_GROUP_BLUETOOTH_SWITCH = "bluetooth_switch"
_GROUP_DEVICE_SETTINGS = "device_settings"
_GROUP_TEST_PLAN = "test_plan"


class ConfigStore:
    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = settings or QSettings(_ORG_NAME, _APP_NAME)

    def load(self) -> AppSettings:
        return self.load_power_cycle()

    def save(self, config: AppSettings) -> None:
        self.save_power_cycle(config)

    def load_power_cycle(self) -> AppSettings:
        defaults = AppSettings()
        self._settings.beginGroup(_GROUP_POWER_CYCLE)
        try:
            legacy_sim = self._read_bool("simulation_mode", defaults.simulation_mode)
            return AppSettings(
                test_count=self._read_int("test_count", defaults.test_count),
                voltage_threshold_v=self._read_float(
                    "voltage_threshold_v", defaults.voltage_threshold_v
                ),
                interval_ms=self._read_int("interval_ms", defaults.interval_ms),
                relay_channel=self._read_int("relay_channel", defaults.relay_channel),
                multimeter_port=self._read_str("multimeter_port", defaults.multimeter_port),
                relay_port=self._read_str("relay_port", defaults.relay_port),
                bt_name_keyword=self._read_str(
                    "bt_name_keyword", defaults.bt_name_keyword
                ),
                bt_mac=self._read_str("bt_mac", defaults.bt_mac),
                bt_match_mode=self._read_bt_mode(defaults.bt_match_mode),
                simulation_multimeter=self._read_bool(
                    "simulation_multimeter", legacy_sim
                ),
                simulation_relay=self._read_bool("simulation_relay", legacy_sim),
                simulation_bluetooth=self._read_bool(
                    "simulation_bluetooth", legacy_sim
                ),
                simulation_mode=legacy_sim,
                state_timeout_ms=self._read_int(
                    "state_timeout_ms", defaults.state_timeout_ms
                ),
                sample_interval_ms=self._read_int(
                    "sample_interval_ms", defaults.sample_interval_ms
                ),
                consecutive_pass_needed=self._read_int(
                    "consecutive_pass_needed", defaults.consecutive_pass_needed
                ),
            )
        finally:
            self._settings.endGroup()

    def save_power_cycle(self, config: AppSettings) -> None:
        self._settings.beginGroup(_GROUP_POWER_CYCLE)
        try:
            self._settings.setValue("test_count", config.test_count)
            self._settings.setValue("voltage_threshold_v", config.voltage_threshold_v)
            self._settings.setValue("interval_ms", config.interval_ms)
            self._settings.setValue("relay_channel", config.relay_channel)
            self._settings.setValue("multimeter_port", config.multimeter_port)
            self._settings.setValue("relay_port", config.relay_port)
            self._settings.setValue("bt_name_keyword", config.bt_name_keyword)
            self._settings.setValue("bt_mac", config.bt_mac)
            self._settings.setValue("bt_match_mode", config.bt_match_mode)
            self._settings.setValue(
                "simulation_multimeter", config.simulation_multimeter
            )
            self._settings.setValue("simulation_relay", config.simulation_relay)
            self._settings.setValue(
                "simulation_bluetooth", config.simulation_bluetooth
            )
            self._settings.setValue(
                "simulation_mode",
                config.simulation_multimeter
                and config.simulation_relay
                and config.simulation_bluetooth,
            )
            self._settings.setValue("state_timeout_ms", config.state_timeout_ms)
            self._settings.setValue("sample_interval_ms", config.sample_interval_ms)
            self._settings.setValue(
                "consecutive_pass_needed", config.consecutive_pass_needed
            )
            self._settings.sync()
        finally:
            self._settings.endGroup()

    def load_bluetooth_connect(self) -> BluetoothConnectSettings:
        defaults = BluetoothConnectSettings()
        self._settings.beginGroup(_GROUP_BLUETOOTH_CONNECT)
        try:
            return BluetoothConnectSettings(
                test_count=self._read_int("test_count", defaults.test_count),
                relay_port=self._read_str("relay_port", defaults.relay_port),
                simulation_relay=self._read_bool(
                    "simulation_relay", defaults.simulation_relay
                ),
                bt_name_keyword=self._read_str(
                    "bt_name_keyword", defaults.bt_name_keyword
                ),
                bt_mac=self._read_str("bt_mac", defaults.bt_mac),
                bt_match_mode=self._read_bt_mode(defaults.bt_match_mode),
                mode_relay_channel=self._read_int(
                    "mode_relay_channel", defaults.mode_relay_channel
                ),
                pairing_relay_channel=self._read_int(
                    "pairing_relay_channel", defaults.pairing_relay_channel
                ),
                pairing_press_ms=self._read_int(
                    "pairing_press_ms", defaults.pairing_press_ms
                ),
                state_timeout_ms=self._read_int(
                    "state_timeout_ms", defaults.state_timeout_ms
                ),
                sample_interval_ms=self._read_int(
                    "sample_interval_ms", defaults.sample_interval_ms
                ),
            )
        finally:
            self._settings.endGroup()

    def save_bluetooth_connect(self, config: BluetoothConnectSettings) -> None:
        self._settings.beginGroup(_GROUP_BLUETOOTH_CONNECT)
        try:
            self._settings.setValue("test_count", config.test_count)
            self._settings.setValue("relay_port", config.relay_port)
            self._settings.setValue("simulation_relay", config.simulation_relay)
            self._settings.setValue("bt_name_keyword", config.bt_name_keyword)
            self._settings.setValue("bt_mac", config.bt_mac)
            self._settings.setValue("bt_match_mode", config.bt_match_mode)
            self._settings.setValue("mode_relay_channel", config.mode_relay_channel)
            self._settings.setValue(
                "pairing_relay_channel", config.pairing_relay_channel
            )
            self._settings.setValue("pairing_press_ms", config.pairing_press_ms)
            self._settings.setValue("state_timeout_ms", config.state_timeout_ms)
            self._settings.setValue("sample_interval_ms", config.sample_interval_ms)
            self._settings.sync()
        finally:
            self._settings.endGroup()

    def load_bluetooth_switch(self) -> BluetoothSwitchSettings:
        defaults = BluetoothSwitchSettings()
        self._settings.beginGroup(_GROUP_BLUETOOTH_SWITCH)
        try:
            return BluetoothSwitchSettings(
                test_count=self._read_int("test_count", defaults.test_count),
                relay_port=self._read_str("relay_port", defaults.relay_port),
                bt_name_keyword=self._read_str(
                    "bt_name_keyword", defaults.bt_name_keyword
                ),
                bt_mac=self._read_str("bt_mac", defaults.bt_mac),
                bt_match_mode=self._read_bt_mode(defaults.bt_match_mode),
                mode_relay_channel=self._read_int(
                    "mode_relay_channel", defaults.mode_relay_channel
                ),
                pairing_relay_channel=self._read_int(
                    "pairing_relay_channel", defaults.pairing_relay_channel
                ),
                pairing_press_ms=self._read_int(
                    "pairing_press_ms", defaults.pairing_press_ms
                ),
                state_timeout_ms=self._read_int(
                    "state_timeout_ms", defaults.state_timeout_ms
                ),
                sample_interval_ms=self._read_int(
                    "sample_interval_ms", defaults.sample_interval_ms
                ),
            )
        finally:
            self._settings.endGroup()

    def save_bluetooth_switch(self, config: BluetoothSwitchSettings) -> None:
        self._settings.beginGroup(_GROUP_BLUETOOTH_SWITCH)
        try:
            self._settings.setValue("test_count", config.test_count)
            self._settings.setValue("relay_port", config.relay_port)
            self._settings.setValue("bt_name_keyword", config.bt_name_keyword)
            self._settings.setValue("bt_mac", config.bt_mac)
            self._settings.setValue("bt_match_mode", config.bt_match_mode)
            self._settings.setValue("mode_relay_channel", config.mode_relay_channel)
            self._settings.setValue(
                "pairing_relay_channel", config.pairing_relay_channel
            )
            self._settings.setValue("pairing_press_ms", config.pairing_press_ms)
            self._settings.setValue("state_timeout_ms", config.state_timeout_ms)
            self._settings.setValue("sample_interval_ms", config.sample_interval_ms)
            self._settings.sync()
        finally:
            self._settings.endGroup()

    def load_device_settings(self) -> DeviceSettings:
        defaults = self._build_legacy_device_settings_defaults()
        self._settings.beginGroup(_GROUP_DEVICE_SETTINGS)
        try:
            target_defaults = defaults.bluetooth_target
            return DeviceSettings(
                multimeter_port=self._read_str(
                    "multimeter_port", defaults.multimeter_port
                ),
                relay_port=self._read_str("relay_port", defaults.relay_port),
                simulation_multimeter=self._read_bool(
                    "simulation_multimeter", defaults.simulation_multimeter
                ),
                simulation_relay=self._read_bool(
                    "simulation_relay", defaults.simulation_relay
                ),
                simulation_bluetooth=self._read_bool(
                    "simulation_bluetooth", defaults.simulation_bluetooth
                ),
                bluetooth_target=BluetoothTargetSettings(
                    bt_name_keyword=self._read_str(
                        "bt_name_keyword", target_defaults.bt_name_keyword
                    ),
                    bt_mac=self._read_str("bt_mac", target_defaults.bt_mac),
                    bt_match_mode=self._read_bt_mode(target_defaults.bt_match_mode),
                ),
            )
        finally:
            self._settings.endGroup()

    def save_device_settings(self, config: DeviceSettings) -> None:
        self._settings.beginGroup(_GROUP_DEVICE_SETTINGS)
        try:
            self._settings.setValue("multimeter_port", config.multimeter_port)
            self._settings.setValue("relay_port", config.relay_port)
            self._settings.setValue(
                "simulation_multimeter", config.simulation_multimeter
            )
            self._settings.setValue("simulation_relay", config.simulation_relay)
            self._settings.setValue(
                "simulation_bluetooth", config.simulation_bluetooth
            )
            self._settings.setValue(
                "bt_name_keyword", config.bluetooth_target.bt_name_keyword
            )
            self._settings.setValue("bt_mac", config.bluetooth_target.bt_mac)
            self._settings.setValue(
                "bt_match_mode", config.bluetooth_target.bt_match_mode
            )
            self._settings.sync()
        finally:
            self._settings.endGroup()

    def load_test_plan(self) -> TestPlanSettings:
        defaults = TestPlanSettings()
        self._settings.beginGroup(_GROUP_TEST_PLAN)
        try:
            return TestPlanSettings(
                enabled_items=self._read_test_items(
                    "enabled_items", defaults.enabled_items
                ),
                mode=self._read_suite_mode(defaults.mode),
                round_count=max(1, self._read_int("round_count", defaults.round_count)),
                bluetooth_switch_method=self._read_bluetooth_switch_method(
                    defaults.bluetooth_switch_method
                ),
            )
        finally:
            self._settings.endGroup()

    def save_test_plan(self, config: TestPlanSettings) -> None:
        enabled_items = [
            item for item in TEST_ITEM_ORDER if item in set(config.enabled_items)
        ]
        self._settings.beginGroup(_GROUP_TEST_PLAN)
        try:
            self._settings.setValue("enabled_items", ",".join(enabled_items))
            self._settings.setValue("mode", config.mode)
            self._settings.setValue("round_count", max(1, config.round_count))
            self._settings.setValue(
                "bluetooth_switch_method", config.bluetooth_switch_method
            )
            self._settings.sync()
        finally:
            self._settings.endGroup()

    def load_preferred_relay_port(self) -> str:
        power_cycle_port = self.load_power_cycle().relay_port.strip()
        if power_cycle_port:
            return power_cycle_port
        return self.load_bluetooth_connect().relay_port.strip()

    def save_preferred_relay_port(self, port: str) -> None:
        normalized_port = port.strip()

        power_cycle = self.load_power_cycle()
        if power_cycle.relay_port != normalized_port:
            power_cycle.relay_port = normalized_port
            self.save_power_cycle(power_cycle)

        bluetooth_connect = self.load_bluetooth_connect()
        if bluetooth_connect.relay_port != normalized_port:
            bluetooth_connect.relay_port = normalized_port
            self.save_bluetooth_connect(bluetooth_connect)

        device_settings = self.load_device_settings()
        if device_settings.relay_port != normalized_port:
            device_settings.relay_port = normalized_port
            self.save_device_settings(device_settings)

    def _build_legacy_device_settings_defaults(self) -> DeviceSettings:
        power_cycle = self.load_power_cycle()
        bluetooth_connect = self.load_bluetooth_connect()
        bluetooth_switch = self.load_bluetooth_switch()

        target_source = power_cycle
        for candidate in (power_cycle, bluetooth_connect, bluetooth_switch):
            if candidate.bt_name_keyword.strip() or candidate.bt_mac.strip():
                target_source = candidate
                break

        return DeviceSettings(
            multimeter_port=power_cycle.multimeter_port,
            relay_port=(
                power_cycle.relay_port.strip()
                or bluetooth_connect.relay_port.strip()
                or bluetooth_switch.relay_port.strip()
            ),
            simulation_multimeter=power_cycle.simulation_multimeter,
            simulation_relay=(
                power_cycle.simulation_relay or bluetooth_connect.simulation_relay
            ),
            simulation_bluetooth=power_cycle.simulation_bluetooth,
            bluetooth_target=BluetoothTargetSettings(
                bt_name_keyword=target_source.bt_name_keyword,
                bt_mac=target_source.bt_mac,
                bt_match_mode=target_source.bt_match_mode,
            ),
        )

    def _read_int(self, key: str, default: int) -> int:
        value = self._settings.value(key, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _read_float(self, key: str, default: float) -> float:
        value = self._settings.value(key, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _read_bool(self, key: str, default: bool) -> bool:
        value = self._settings.value(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        if isinstance(value, (int, float)):
            return bool(value)
        return default

    def _read_str(self, key: str, default: str) -> str:
        value = self._settings.value(key, default)
        return "" if value is None else str(value)

    def _read_bt_mode(self, default: str) -> str:
        mode = self._read_str("bt_match_mode", default)
        if mode not in {"name_or_mac", "name_and_mac"}:
            return default
        return mode

    def _read_test_items(
        self, key: str, default: tuple[TestItemId, ...]
    ) -> tuple[TestItemId, ...]:
        if not self._settings.contains(key):
            return default
        raw = self._read_str(key, "")
        requested = {item.strip() for item in raw.split(",") if item.strip()}
        items = tuple(item for item in TEST_ITEM_ORDER if item in requested)
        return items

    def _read_suite_mode(self, default: SuiteMode) -> SuiteMode:
        mode = self._read_str("mode", default)
        if mode not in {"sequential_items", "round_robin"}:
            return default
        return mode  # type: ignore[return-value]

    def _read_bluetooth_switch_method(
        self, default: BluetoothSwitchMethod
    ) -> BluetoothSwitchMethod:
        method = self._read_str("bluetooth_switch_method", default)
        if method not in {"adapter", "ui"}:
            return default
        return method  # type: ignore[return-value]


def to_settings_snapshot(config: AppSettings) -> dict[str, Any]:
    return {
        "test_count": config.test_count,
        "voltage_threshold_v": config.voltage_threshold_v,
        "interval_ms": config.interval_ms,
        "relay_channel": config.relay_channel,
        "multimeter_port": config.multimeter_port,
        "relay_port": config.relay_port,
        "bt_name_keyword": config.bt_name_keyword,
        "bt_mac": config.bt_mac,
        "bt_match_mode": config.bt_match_mode,
        "simulation_multimeter": config.simulation_multimeter,
        "simulation_relay": config.simulation_relay,
        "simulation_bluetooth": config.simulation_bluetooth,
        "simulation_mode": config.simulation_mode,
        "state_timeout_ms": config.state_timeout_ms,
        "sample_interval_ms": config.sample_interval_ms,
        "consecutive_pass_needed": config.consecutive_pass_needed,
    }


def to_bluetooth_connect_snapshot(config: BluetoothConnectSettings) -> dict[str, Any]:
    return {
        "test_count": config.test_count,
        "relay_port": config.relay_port,
        "simulation_relay": config.simulation_relay,
        "bt_name_keyword": config.bt_name_keyword,
        "bt_mac": config.bt_mac,
        "bt_match_mode": config.bt_match_mode,
        "mode_relay_channel": config.mode_relay_channel,
        "pairing_relay_channel": config.pairing_relay_channel,
        "pairing_press_ms": config.pairing_press_ms,
        "state_timeout_ms": config.state_timeout_ms,
        "sample_interval_ms": config.sample_interval_ms,
    }

def to_bluetooth_switch_snapshot(config: BluetoothSwitchSettings) -> dict[str, Any]:
    return {
        "test_count": config.test_count,
        "relay_port": config.relay_port,
        "bt_name_keyword": config.bt_name_keyword,
        "bt_mac": config.bt_mac,
        "bt_match_mode": config.bt_match_mode,
        "mode_relay_channel": config.mode_relay_channel,
        "pairing_relay_channel": config.pairing_relay_channel,
        "pairing_press_ms": config.pairing_press_ms,
        "state_timeout_ms": config.state_timeout_ms,
        "sample_interval_ms": config.sample_interval_ms,
    }


def to_device_settings_snapshot(config: DeviceSettings) -> dict[str, Any]:
    return {
        "multimeter_port": config.multimeter_port,
        "relay_port": config.relay_port,
        "simulation_multimeter": config.simulation_multimeter,
        "simulation_relay": config.simulation_relay,
        "simulation_bluetooth": config.simulation_bluetooth,
        "bt_name_keyword": config.bluetooth_target.bt_name_keyword,
        "bt_mac": config.bluetooth_target.bt_mac,
        "bt_match_mode": config.bluetooth_target.bt_match_mode,
    }


def to_test_plan_snapshot(config: TestPlanSettings) -> dict[str, Any]:
    return {
        "enabled_items": list(config.enabled_items),
        "mode": config.mode,
        "round_count": config.round_count,
        "bluetooth_switch_method": config.bluetooth_switch_method,
    }
