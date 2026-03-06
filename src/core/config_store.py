from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSettings

from src.core.types import AppSettings

_ORG_NAME = "RJHZ"
_APP_NAME = "MouseAutomationTool"
_GROUP_NAME = "power_cycle"


class ConfigStore:
    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = settings or QSettings(_ORG_NAME, _APP_NAME)

    def load(self) -> AppSettings:
        defaults = AppSettings()
        self._settings.beginGroup(_GROUP_NAME)
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

    def save(self, config: AppSettings) -> None:
        self._settings.beginGroup(_GROUP_NAME)
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
