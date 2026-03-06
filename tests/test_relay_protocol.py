from __future__ import annotations

import unittest

from src.core.relay_lcus88 import build_switch_command, parse_relay_status


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


if __name__ == "__main__":
    unittest.main()
