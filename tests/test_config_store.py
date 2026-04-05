from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QSettings

from src.core.config_store import ConfigStore
from src.core.types import AppSettings, BluetoothConnectSettings


class TestConfigStore(unittest.TestCase):
    def test_save_and_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ini_path = Path(tmpdir) / "settings.ini"
            qsettings = QSettings(str(ini_path), QSettings.Format.IniFormat)
            store = ConfigStore(settings=qsettings)

            original = AppSettings(
                test_count=55,
                voltage_threshold_v=2.7,
                interval_ms=1800,
                relay_channel=3,
                multimeter_port="COM7",
                relay_port="COM8",
                bt_name_keyword="MX",
                bt_mac="00:11:22:AA:BB:CC",
                bt_match_mode="name_and_mac",
                simulation_multimeter=True,
                simulation_relay=False,
                simulation_bluetooth=True,
                simulation_mode=False,
                state_timeout_ms=6500,
                sample_interval_ms=150,
                consecutive_pass_needed=3,
            )
            store.save(original)
            loaded = store.load()

            self.assertEqual(loaded.test_count, original.test_count)
            self.assertEqual(loaded.voltage_threshold_v, original.voltage_threshold_v)
            self.assertEqual(loaded.interval_ms, original.interval_ms)
            self.assertEqual(loaded.relay_channel, original.relay_channel)
            self.assertEqual(loaded.multimeter_port, original.multimeter_port)
            self.assertEqual(loaded.relay_port, original.relay_port)
            self.assertEqual(loaded.bt_name_keyword, original.bt_name_keyword)
            self.assertEqual(loaded.bt_mac, original.bt_mac)
            self.assertEqual(loaded.bt_match_mode, original.bt_match_mode)
            self.assertEqual(
                loaded.simulation_multimeter, original.simulation_multimeter
            )
            self.assertEqual(loaded.simulation_relay, original.simulation_relay)
            self.assertEqual(
                loaded.simulation_bluetooth, original.simulation_bluetooth
            )
            self.assertEqual(loaded.simulation_mode, original.simulation_mode)
            self.assertEqual(loaded.state_timeout_ms, original.state_timeout_ms)
            self.assertEqual(loaded.sample_interval_ms, original.sample_interval_ms)
            self.assertEqual(
                loaded.consecutive_pass_needed, original.consecutive_pass_needed
            )

    def test_legacy_simulation_mode_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ini_path = Path(tmpdir) / "settings.ini"
            qsettings = QSettings(str(ini_path), QSettings.Format.IniFormat)
            qsettings.beginGroup("power_cycle")
            qsettings.setValue("simulation_mode", True)
            qsettings.endGroup()
            qsettings.sync()

            store = ConfigStore(settings=qsettings)
            loaded = store.load()
            self.assertTrue(loaded.simulation_multimeter)
            self.assertTrue(loaded.simulation_relay)
            self.assertTrue(loaded.simulation_bluetooth)

    def test_save_and_load_bluetooth_connect_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ini_path = Path(tmpdir) / "settings.ini"
            qsettings = QSettings(str(ini_path), QSettings.Format.IniFormat)
            store = ConfigStore(settings=qsettings)

            original = BluetoothConnectSettings(
                test_count=22,
                relay_port="COM9",
                bt_name_keyword="LOWA Mouse",
                bt_mac="AA:BB:CC:11:22:33",
                bt_match_mode="name_and_mac",
                mode_relay_channel=4,
                pairing_relay_channel=5,
                pairing_press_ms=3500,
                state_timeout_ms=18000,
                sample_interval_ms=250,
            )
            store.save_bluetooth_connect(original)
            loaded = store.load_bluetooth_connect()

            self.assertEqual(loaded.test_count, original.test_count)
            self.assertEqual(loaded.relay_port, original.relay_port)
            self.assertEqual(loaded.bt_name_keyword, original.bt_name_keyword)
            self.assertEqual(loaded.bt_mac, original.bt_mac)
            self.assertEqual(loaded.bt_match_mode, original.bt_match_mode)
            self.assertEqual(loaded.mode_relay_channel, original.mode_relay_channel)
            self.assertEqual(
                loaded.pairing_relay_channel, original.pairing_relay_channel
            )
            self.assertEqual(loaded.pairing_press_ms, original.pairing_press_ms)
            self.assertEqual(loaded.state_timeout_ms, original.state_timeout_ms)
            self.assertEqual(loaded.sample_interval_ms, original.sample_interval_ms)


if __name__ == "__main__":
    unittest.main()
