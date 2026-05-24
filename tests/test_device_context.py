from __future__ import annotations

import unittest

from src.core.device_context import DeviceContext
from src.core.types import DeviceSettings


class TestDeviceContext(unittest.TestCase):
    def test_real_bluetooth_manager_uses_uncached_probe(self) -> None:
        context = DeviceContext()
        try:
            probe = context.bluetooth_probe_real
            self.assertEqual(probe._inventory_cache_ttl_sec, 0.0)
            self.assertEqual(probe._target_cache_ttl_sec, 0.0)

            manager = context.active_bluetooth_manager(
                DeviceSettings(simulation_bluetooth=False)
            )
            self.assertIs(manager._probe, probe)
        finally:
            context.shutdown()


if __name__ == "__main__":
    unittest.main()
