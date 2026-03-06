from __future__ import annotations

import threading
import time
import unittest

from src.core.simulators import SimulatedBluetoothProbe, SimulatedMultimeter, SimulatedRelay
from src.core.test_engine import PowerCycleRunner
from src.core.types import AppSettings, VerificationPolicy


class TestEngineSimulation(unittest.TestCase):
    def test_simulated_bluetooth_query_devices_has_default_entry(self) -> None:
        relay = SimulatedRelay()
        bt = SimulatedBluetoothProbe(relay, target_channel=1, transition_samples=0)
        devices = bt.query_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].name, "SimMouse")

    def test_simulation_runner_success(self) -> None:
        relay = SimulatedRelay()
        meter = SimulatedMultimeter(relay, target_channel=1)
        bt = SimulatedBluetoothProbe(relay, target_channel=1, transition_samples=0)

        settings = AppSettings(
            test_count=10,
            voltage_threshold_v=3.0,
            interval_ms=10,
            relay_channel=1,
            simulation_multimeter=True,
            simulation_relay=True,
            simulation_bluetooth=True,
            simulation_mode=True,
            state_timeout_ms=1000,
            sample_interval_ms=20,
            consecutive_pass_needed=1,
        )
        policy = VerificationPolicy(
            state_timeout_ms=settings.state_timeout_ms,
            sample_interval_ms=settings.sample_interval_ms,
            consecutive_pass_needed=settings.consecutive_pass_needed,
        )

        runner = PowerCycleRunner(relay, meter, bt, settings, policy)
        summary = runner.run()
        self.assertEqual(summary.success_count, 10)
        self.assertEqual(summary.fail_count, 0)

    def test_stop_requested_works_quickly(self) -> None:
        relay = SimulatedRelay()
        meter = SimulatedMultimeter(relay, target_channel=1)
        bt = SimulatedBluetoothProbe(relay, target_channel=1, transition_samples=0)
        settings = AppSettings(
            test_count=500,
            voltage_threshold_v=3.0,
            interval_ms=50,
            relay_channel=1,
            simulation_multimeter=True,
            simulation_relay=True,
            simulation_bluetooth=True,
            simulation_mode=True,
            state_timeout_ms=2000,
            sample_interval_ms=50,
            consecutive_pass_needed=1,
        )
        policy = VerificationPolicy(
            state_timeout_ms=settings.state_timeout_ms,
            sample_interval_ms=settings.sample_interval_ms,
            consecutive_pass_needed=settings.consecutive_pass_needed,
        )

        runner = PowerCycleRunner(relay, meter, bt, settings, policy)
        holder: dict[str, object] = {}

        def _run() -> None:
            holder["summary"] = runner.run()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        time.sleep(0.3)
        runner.stop()
        t.join(timeout=1.0)

        self.assertFalse(t.is_alive(), "runner.stop() 后线程应在1秒内结束")
        summary = holder.get("summary")
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertLess(summary.success_count + summary.fail_count, 500)


if __name__ == "__main__":
    unittest.main()
