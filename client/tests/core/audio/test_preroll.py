from core.audio.preroll import PrerollBuffer


def _make_buffer(seconds: float, sample_rate: int, frames_per_buffer: int) -> PrerollBuffer:
    return PrerollBuffer(
        seconds=seconds, sample_rate=sample_rate, frames_per_buffer=frames_per_buffer
    )


def test_empty_buffer_returns_empty_bytes():
    buf = _make_buffer(seconds=1.0, sample_rate=16000, frames_per_buffer=1600)
    assert buf.get() == b""


def test_append_accumulates_in_order():
    buf = _make_buffer(seconds=1.0, sample_rate=16000, frames_per_buffer=1600)
    buf.append(b"aaaa")
    buf.append(b"bbbb")
    assert buf.get() == b"aaaabbbb"


def test_maxlen_discards_oldest_frames():
    # seconds=1.0, sample_rate=16000, frames_per_buffer=1600 -> maxlen = 10 frames
    buf = _make_buffer(seconds=1.0, sample_rate=16000, frames_per_buffer=1600)
    frames = [f"{i:04d}".encode() for i in range(12)]
    for frame in frames:
        buf.append(frame)
    assert buf.get() == b"".join(frames[-10:])


def test_maxlen_property_reflects_configured_window():
    buf = _make_buffer(seconds=1.0, sample_rate=16000, frames_per_buffer=1600)
    assert buf.maxlen == 10


def test_clear_empties_the_buffer():
    buf = _make_buffer(seconds=1.0, sample_rate=16000, frames_per_buffer=1600)
    buf.append(b"aaaa")
    buf.clear()
    assert buf.get() == b""
