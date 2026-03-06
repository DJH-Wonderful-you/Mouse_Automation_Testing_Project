from __future__ import annotations

import unittest

from src.core.multimeter_victor86e import Victor86EProtocolParser


class TestVictor86EProtocolParser(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = Victor86EProtocolParser()

    def test_parse_normal_voltage_packet(self) -> None:
        packet = bytes(
            [0x32, 0xB0, 0xB1, 0xB2, 0xB3, 0xB4, 0x3B, 0x00, 0x00, 0x00, 0x08, 0x00, 0x0D, 0x0A]
        )
        result = self.parser.parse(packet)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.function, "V")
        self.assertAlmostEqual(float(result.value), 12.34, places=3)

    def test_parse_invalid_length_returns_none(self) -> None:
        self.assertIsNone(self.parser.parse(b"\x00\x01\x02"))

    def test_parse_unknown_function_code(self) -> None:
        packet = bytes(
            [0x31, 0xB0, 0xB0, 0xB0, 0xB0, 0xB1, 0x99, 0x00, 0x00, 0x00, 0x08, 0x00, 0x0D, 0x0A]
        )
        result = self.parser.parse(packet)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("未知", result.function)

    def test_parse_ol_packet(self) -> None:
        packet = bytes(
            [0x31, 0xB0, 0xB1, 0xB2, 0xB3, 0xB4, 0x3B, 0x01, 0x00, 0x00, 0x08, 0x00, 0x0D, 0x0A]
        )
        result = self.parser.parse(packet)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.is_ol)
        self.assertEqual(result.value, "OL")


if __name__ == "__main__":
    unittest.main()
