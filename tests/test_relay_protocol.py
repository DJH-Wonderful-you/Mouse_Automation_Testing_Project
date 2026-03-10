from __future__ import annotations

import unittest

from src.core.relay_lcus88 import LCUSRelay, build_switch_command, parse_relay_status


class TestRelayProtocol(unittest.TestCase):
    def test_build_switch_command(self) -> None:
        self.assertEqual(build_switch_command(1, True), bytes([0xA0, 0x01, 0x01, 0xA2]))
        self.assertEqual(build_switch_command(1, False), bytes([0xA0, 0x01, 0x00, 0xA1]))
        self.assertEqual(build_switch_command(8, True), bytes([0xA0, 0x08, 0x01, 0xA9]))

    def test_parse_status_direct_bytes(self) -> None:
        states = parse_relay_status(bytes([1, 0, 1, 0, 1, 0, 1, 0]))
        self.assertTrue(states[1])
        self.assertFalse(states[2])
        self.assertTrue(states[7])
        self.assertFalse(states[8])

    def test_parse_status_bitmask(self) -> None:
        states = parse_relay_status(bytes([0b00000101]))
        self.assertTrue(states[1])
        self.assertFalse(states[2])
        self.assertTrue(states[3])

    def test_parse_status_ascii_hex(self) -> None:
        raw = b"01 00 01 00 00 00 00 01"
        states = parse_relay_status(raw)
        self.assertTrue(states[1])
        self.assertTrue(states[3])
        self.assertTrue(states[8])
        self.assertFalse(states[2])

    def test_query_channel_state_fallback_to_cache(self) -> None:
        class _CachedOnlyRelay(LCUSRelay):
            @property
            def is_connected(self) -> bool:  # type: ignore[override]
                return True

            def query_status(self) -> dict[int, bool]:
                raise RuntimeError("query not supported")

        relay = _CachedOnlyRelay()
        relay._cached_states[1] = True
        self.assertTrue(relay.query_channel_state(1))
        with self.assertRaises(RuntimeError):
            relay.query_channel_state(2)


if __name__ == "__main__":
    unittest.main()
