import struct

import pytest

from core.audio.framing import int16_to_float32


def test_zero_stays_zero():
    data = struct.pack("<1h", 0)
    result = int16_to_float32(data)
    assert struct.unpack("<1f", result)[0] == 0.0


def test_max_int16_normalizes_correctly():
    data = struct.pack("<1h", 32767)
    result = int16_to_float32(data)
    value = struct.unpack("<1f", result)[0]
    assert value == pytest.approx(32767 / 32768.0)


def test_min_int16_normalizes_to_minus_one():
    data = struct.pack("<1h", -32768)
    result = int16_to_float32(data)
    value = struct.unpack("<1f", result)[0]
    assert value == pytest.approx(-1.0)


def test_preserves_sample_count():
    data = struct.pack("<4h", 100, -100, 200, -200)
    result = int16_to_float32(data)
    assert len(result) == 4 * 4  # 4 samples * 4 bytes/sample (float32)
