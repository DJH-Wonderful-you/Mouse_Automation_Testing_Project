from __future__ import annotations

import threading
import time
import unittest

from src.core.bluetooth_connect_engine import BluetoothConnectRunner
from src.core.bluetooth_pairing import BluetoothActionResult, SimulatedBluetoothManager
from src.core.simulators import SimulatedRelay
from src.core.types import BluetoothConnectCycleResult, BluetoothConnectSettings


class _CleanupFailsManager:
    def __init__(self) -> None:
        self._paired = False
        self._remove_calls = 0

    def query_paired_devices(self, name_keyword: str, mac: str, mode: str):
        _ = name_keyword, mac, mode
        return [object()] if self._paired else []

    def is_target_connected(self, name_keyword: str, mac: str, mode: str):
        _ = name_keyword, mac, mode
        return self._paired, [object()] if self._paired else []

    def pair_target(
        self,
        name_keyword: str,
        mac: str,
        mode: str,
        *,
        timeout_sec: float,
        sample_interval_sec: float,
        log_cb=None,
    ) -> BluetoothActionResult:
        _ = name_keyword, mac, mode, timeout_sec, sample_interval_sec, log_cb
        self._paired = True
        return BluetoothActionResult(ok=True, matched=[])

    def remove_target(
        self,
        name_keyword: str,
        mac: str,
        mode: str,
        *,
        timeout_sec: float,
        sample_interval_sec: float,
        log_cb=None,
    ) -> BluetoothActionResult:
        _ = name_keyword, mac, mode, timeout_sec, sample_interval_sec, log_cb
        self._remove_calls += 1
        if self._remove_calls >= 1:
            return BluetoothActionResult(ok=False, reason="cleanup failed")
        self._paired = False
        return BluetoothActionResult(ok=True, matched=[])


class TestBluetoothConnectionEngine(unittest.TestCase):
    def _build_settings(self, **overrides: object) -> BluetoothConnectSettings:
        settings = BluetoothConnectSettings(
            test_count=3,
            bt_name_keyword="SimMouse",
            bt_mac="00:11:22:AA:BB:CC",
            bt_match_mode="name_and_mac",
            mode_relay_channel=1,
            pairing_relay_channel=2,
            pairing_press_ms=10,
            state_timeout_ms=500,
            sample_interval_ms=10,
        )
        for key, value in overrides.items():
            setattr(settings, key, value)
        return settings

    def test_runner_success_multiple_cycles(self) -> None:
        relay = SimulatedRelay()
        manager = SimulatedBluetoothManager()
        settings = self._build_settings(test_count=3)

        results: list[BluetoothConnectCycleResult] = []
        runner = BluetoothConnectRunner(
            relay,
            manager,
            settings,
            cycle_cb=results.append,
        )
        summary = runner.run()

        self.assertEqual(summary.success_count, 3)
        self.assertEqual(summary.fail_count, 0)
        self.assertEqual(len(results), 3)
        self.assertTrue(all(result.success for result in results))
        self.assertTrue(relay.query_channel_state(settings.mode_relay_channel))
        self.assertFalse(relay.query_channel_state(settings.pairing_relay_channel))

    def test_runner_removes_initially_paired_device_before_cycle(self) -> None:
        relay = SimulatedRelay()
        manager = SimulatedBluetoothManager()
        manager.seed_paired_device(connected=True)
        settings = self._build_settings(test_count=1)

        results: list[BluetoothConnectCycleResult] = []
        runner = BluetoothConnectRunner(
            relay,
            manager,
            settings,
            cycle_cb=results.append,
        )
        summary = runner.run()

        self.assertEqual(summary.success_count, 1)
        self.assertEqual(summary.fail_count, 0)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].paired_before_cycle)
        self.assertTrue(results[0].removed_before_cycle)
        self.assertTrue(results[0].removed_after_cycle)

    def test_runner_marks_cycle_failed_when_cleanup_remove_fails(self) -> None:
        relay = SimulatedRelay()
        manager = _CleanupFailsManager()
        settings = self._build_settings(test_count=1)

        results: list[BluetoothConnectCycleResult] = []
        runner = BluetoothConnectRunner(
            relay,
            manager,
            settings,
            cycle_cb=results.append,
        )
        summary = runner.run()

        self.assertEqual(summary.success_count, 0)
        self.assertEqual(summary.fail_count, 1)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].success)
        self.assertIn("删除配对失败", results[0].reason)

    def test_stop_requested_works_during_pairing_pulse(self) -> None:
        relay = SimulatedRelay()
        manager = SimulatedBluetoothManager()
        settings = self._build_settings(
            test_count=200,
            pairing_press_ms=1000,
            sample_interval_ms=20,
        )
        runner = BluetoothConnectRunner(relay, manager, settings)
        holder: dict[str, object] = {}

        def _run() -> None:
            holder["summary"] = runner.run()

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        time.sleep(0.2)
        runner.stop()
        thread.join(timeout=1.5)

        self.assertFalse(thread.is_alive(), "runner.stop() 后线程应在 1.5 秒内结束")
        summary = holder.get("summary")
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertLess(summary.success_count + summary.fail_count, settings.test_count)


if __name__ == "__main__":
    unittest.main()
