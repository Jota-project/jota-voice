"""Tests offline del TermuxBackend.

No abre streams reales de PyAudio (no disponible en Mac/Linux); captura
los bytes que el backend entrega a `stream.write()` para validar la
lógica de alineamiento a muestras int16.
"""
from __future__ import annotations

import asyncio

from config import AudioConfig
from backends.audio_termux import TermuxBackend


def _cfg(**kwargs) -> AudioConfig:
    base = dict(
        sample_rate=16000,
        channels=1,
        frames_per_buffer=512,
        preroll_seconds=1.5,
        vad_rms_threshold=200.0,
        input_device=None,
        output_device=None,
    )
    base.update(kwargs)
    return AudioConfig(**base)


class _FakeStream:
    """Captura los bytes que se pasan a `write()` para aserciones."""

    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes, num_frames: int | None = None) -> None:  # noqa: ARG002
        self.written.append(bytes(data))

    def stop_stream(self) -> None:
        pass

    def close(self) -> None:
        pass


class _FakePa:
    """Suficiente para que TermuxBackend crea que tiene un PyAudio."""

    paInt16 = 16

    def terminate(self) -> None:
        pass


def _backend_with_fake_stream() -> tuple[TermuxBackend, _FakeStream]:
    be = TermuxBackend(_cfg())
    be._pa = _FakePa()  # type: ignore[assignment]
    stream = _FakeStream()
    be._stream = stream  # type: ignore[assignment]
    return be, stream


def test_play_chunk_impar_no_pierde_bytes() -> None:
    """Fix de raíz: el chunking de red no garantiza alinear cada mensaje TTS
    a un número par de bytes (tamaño de una muestra int16). play_chunk debe
    arrastrar el byte suelto al siguiente chunk en vez de dejarlo pasar a
    PyAudio.Stream.write(), que internamente computa frames como
    len(data) // (channels * sample_width) y descarta silenciosamente el
    último byte si la longitud es impar — equivalente en la práctica al
    patrón `len(bytes) // 2` que perdía muestras en el backend Termux."""
    be, stream = _backend_with_fake_stream()

    async def _run():
        await be.play_chunk(bytes([1, 2, 3]))  # 3 bytes: impar
        await be.play_chunk(bytes([4, 5]))     # 2 bytes: par

    asyncio.run(_run())

    # Todo lo escrito a PyAudio debe tener longitud par (alineado a int16)
    # y la concatenación más el carry pendiente debe preservar todos los
    # bytes de entrada en orden, sin reordenamientos ni descarte.
    written_concat = b"".join(stream.written)
    assert all(len(c) % 2 == 0 for c in stream.written), (
        f"chunk impar escrito a PyAudio: {[len(c) for c in stream.written]}"
    )
    assert written_concat + be._enqueue_carry == bytes([1, 2, 3, 4, 5])


def test_play_notification_no_pierde_bytes() -> None:
    """El beep sintetizado se genera como int16 (longitud par por
    construcción), pero el path debe pasar por la misma lógica de
    alineamiento por simetría y para no tener dos rutas divergentes."""
    be, stream = _backend_with_fake_stream()

    asyncio.run(be.play_notification())

    assert len(stream.written) >= 1
    assert all(len(c) % 2 == 0 for c in stream.written), (
        f"chunk impar escrito a PyAudio: {[len(c) for c in stream.written]}"
    )


def test_drain_resets_carry() -> None:
    """Tras drain() el byte suelto pendiente se descarta: ya no hay un
    siguiente chunk al que pegárselo y forzar su reproducción causaría un
    sample arbitrario al final del turno."""
    be, _ = _backend_with_fake_stream()
    be._enqueue_carry = bytes([0xAB])

    asyncio.run(be.drain())

    assert be._enqueue_carry == b""


def test_stop_resets_carry() -> None:
    """Igual que drain: stop() debe limpiar el estado de carry para que
    un nuevo start() no empiece arrastrando bytes de la sesión anterior."""
    be, _ = _backend_with_fake_stream()
    be._enqueue_carry = bytes([0xAB])

    asyncio.run(be.stop())

    assert be._enqueue_carry == b""


def test_reset_resets_carry() -> None:
    """reset() lo llama PlaybackEngine.reset() entre turnos; debe limpiar
    el carry para que un byte suelto de un turno cancelado a medias no se
    cuele en el siguiente turno como ruido audible."""
    be, _ = _backend_with_fake_stream()
    be._enqueue_carry = bytes([0xAB])

    be.reset()

    assert be._enqueue_carry == b""


def test_play_chunk_un_solo_byte_se_acumula_en_carry() -> None:
    """Un chunk de 1 byte no produce ninguna muestra completa: el byte
    debe quedar en carry sin escribirse a PyAudio, y un chunk posterior
    de 1 byte más debe completarlo como una muestra int16."""
    be, stream = _backend_with_fake_stream()

    async def _run():
        await be.play_chunk(bytes([0x42]))          # 1 byte: nada escribible
        assert stream.written == []
        assert be._enqueue_carry == bytes([0x42])
        await be.play_chunk(bytes([0xCD]))          # completa la muestra

    asyncio.run(_run())

    assert b"".join(stream.written) == bytes([0x42, 0xCD])
    assert be._enqueue_carry == b""


def test_play_chunk_par_luego_impar() -> None:
    """Inverso del test principal: si el primer chunk ya está alineado,
    no debe quedar nada en carry y el chunk impar siguiente arrastra
    su byte suelto al subsiguiente."""
    be, stream = _backend_with_fake_stream()

    async def _run():
        await be.play_chunk(bytes([1, 2]))      # par → escribe, carry=""
        await be.play_chunk(bytes([3]))         # impar → carry=[3]
        await be.play_chunk(bytes([4, 5, 6]))   # 3+3=6 par → escribe [3,4,5,6]

    asyncio.run(_run())

    assert b"".join(stream.written) == bytes([1, 2, 3, 4, 5, 6])
    assert be._enqueue_carry == b""


def test_play_chunk_vacio_con_carry_pendiente_no_escribe() -> None:
    """Una llamada vacía (b"") debe ser no-op incluso con carry pendiente:
    ni escribe ni descarta el byte suelto (otra llamada posterior podría
    completarlo)."""
    be, stream = _backend_with_fake_stream()
    be._enqueue_carry = bytes([0xAB])

    asyncio.run(be.play_chunk(b""))

    assert stream.written == []
    assert be._enqueue_carry == bytes([0xAB])
