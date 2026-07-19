"""Tests de OWWClient — usan un servidor TCP real (no mocks) para el protocolo Wyoming."""
from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock

from config import AudioConfig, OWWConfig
from backends.oww_client import OWWClient


class _EmptyAudio:
    """AudioBackend fake: la cola nunca produce frames, _send_audio_loop se queda esperando."""

    def get_queue(self) -> asyncio.Queue:
        return asyncio.Queue()

    def get_oww_queue(self) -> asyncio.Queue:
        return asyncio.Queue()


async def _drop_after_handshake_server(disconnects: list) -> asyncio.base_events.Server:
    """Servidor Wyoming fake: completa el handshake (detect + audio-start) y
    cierra la conexión inmediatamente — simula un 'Connection reset' real
    como el visto en producción con Docker."""

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readline()  # detect
        await reader.readline()  # audio-start
        disconnects.append(asyncio.get_running_loop().time())
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    return await asyncio.start_server(handler, "127.0.0.1", 0)


async def _test_run_forever_aplica_backoff_tras_desconexion_post_conexion() -> None:
    """Si OWW cierra la conexión justo tras conectar (no un fallo de connect()
    inicial), run_forever debe esperar el backoff configurado antes de
    reconectar — no debe entrar en un hot-loop de reconexión sin límite."""
    disconnects: list = []
    server = await _drop_after_handshake_server(disconnects)
    port = server.sockets[0].getsockname()[1]

    async with server:
        cfg = OWWConfig(
            host="127.0.0.1",
            port=port,
            wake_words=["ok_nabu"],
            reconnect_backoff_s=[0.2, 0.2],
        )
        client = OWWClient(cfg)

        async def _on_wake(_name: str) -> None:
            pass

        task = asyncio.create_task(client.run_forever(_EmptyAudio(), _on_wake))
        start = asyncio.get_running_loop().time()
        try:
            while len(disconnects) < 3 and asyncio.get_running_loop().time() - start < 2.0:
                await asyncio.sleep(0.01)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert len(disconnects) >= 3, f"Esperaba >=3 desconexiones, hubo {len(disconnects)}"
        elapsed = disconnects[2] - disconnects[0]
        # Sin backoff, 3 desconexiones ocurren en <50ms (hot-loop). Con el
        # backoff de 0.2s aplicado entre reconexiones, deben mediar ~0.4s
        # entre la 1ª y la 3ª desconexión.
        assert elapsed >= 0.35, (
            f"Reconectó demasiado rápido tras la desconexión post-conexión "
            f"(hot-loop sin backoff): {elapsed:.3f}s entre 3 desconexiones"
        )


def test_run_forever_aplica_backoff_tras_desconexion_post_conexion() -> None:
    asyncio.run(_test_run_forever_aplica_backoff_tras_desconexion_post_conexion())


async def _detection_burst_server(name: str, n_burst: int, gap_s: float) -> asyncio.base_events.Server:
    """Servidor Wyoming fake: completa el handshake y envía ``n_burst``
    eventos 'detection' de golpe (sin delay) — simula el comportamiento real
    de wyoming-openwakeword, que dispara Detection en cada chunk de audio
    por encima del threshold mientras dura la locución, no una vez por
    locución. Tras ``gap_s`` (mayor que la ventana de debounce bajo prueba),
    envía un evento más para simular una segunda locución genuina."""

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readline()  # detect
        await reader.readline()  # audio-start

        line = json.dumps({"type": "detection", "data": {"name": name}}).encode() + b"\n"
        for _ in range(n_burst):
            writer.write(line)
        await writer.drain()

        await asyncio.sleep(gap_s)
        writer.write(line)
        await writer.drain()

        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    return await asyncio.start_server(handler, "127.0.0.1", 0)


async def _test_wait_for_detection_ignora_detecciones_duplicadas_de_la_misma_wake_word() -> None:
    """wyoming-openwakeword dispara un evento Detection por cada chunk de
    audio por encima del threshold — una sola locución de ~1s puede generar
    8-15 eventos seguidos (visto en producción: 9 'Triggered' en el mismo
    segundo para un solo 'ok jota'). Sin debounce, cada uno dispara
    on_wake_word por separado, causando turnos fantasma (p.ej. reentra en
    RECORDING justo tras interrumpir RESPONDING, sin que el usuario haya
    dicho nada nuevo). wait_for_detection debe colapsar una ráfaga de
    detecciones de la misma wake word en una sola, y solo volver a aceptar
    otra tras la ventana de debounce."""
    server = await _detection_burst_server("ok_jota", n_burst=5, gap_s=0.2)
    port = server.sockets[0].getsockname()[1]

    async with server:
        cfg = OWWConfig(
            host="127.0.0.1",
            port=port,
            wake_words=["ok_jota"],
            debounce_s=0.1,
        )
        client = OWWClient(cfg)
        await client.connect()

        detections: list = []

        async def _collect() -> None:
            try:
                while True:
                    name = await client.wait_for_detection()
                    detections.append(name)
            except ConnectionError:
                pass  # el servidor fake cierra tras la 2ª detección — esperado

        task = asyncio.create_task(_collect())
        try:
            await asyncio.sleep(0.4)  # ráfaga inicial + gap_s(0.2) + 2ª detección
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert detections == ["ok_jota", "ok_jota"], (
            f"Esperaba 2 detecciones (ráfaga colapsada + 2ª tras el gap), "
            f"hubo {len(detections)}: {detections}"
        )


def test_wait_for_detection_ignora_detecciones_duplicadas_de_la_misma_wake_word() -> None:
    asyncio.run(_test_wait_for_detection_ignora_detecciones_duplicadas_de_la_misma_wake_word())


# ---------------------------------------------------------------------------
# Issue #14: OWW debe reflejar rate/channels de AudioConfig en los eventos
# Wyoming (audio-start + audio-chunk) en lugar de hardcodear 16000/mono.
# ---------------------------------------------------------------------------


async def _capture_audio_start_server(captured: list) -> asyncio.base_events.Server:
    """Servidor fake: completa handshake y captura el JSON de audio-start
    recibido. Cierra la conexión al terminar."""

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readline()  # detect
        audio_start_line = await reader.readline()
        try:
            captured.append(json.loads(audio_start_line.decode().strip()))
        except json.JSONDecodeError:
            captured.append({})
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    return await asyncio.start_server(handler, "127.0.0.1", 0)


async def _capture_audio_chunk_server(captured: list) -> asyncio.base_events.Server:
    """Servidor fake: completa handshake y captura el JSON del primer audio-chunk
    recibido, junto con su payload (entero si existe). Cierra la conexión al terminar."""

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readline()  # detect
        await reader.readline()  # audio-start
        chunk_line = await reader.readline()
        try:
            msg = json.loads(chunk_line.decode().strip())
        except json.JSONDecodeError:
            msg = {}
        payload_len = msg.get("payload_length", 0)
        if payload_len > 0:
            payload = await reader.readexactly(payload_len)
        else:
            payload = b""
        captured.append((msg, payload))
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    return await asyncio.start_server(handler, "127.0.0.1", 0)


async def _test_audio_start_refleja_rate_y_channels_de_audio_config() -> None:
    """Sin audio_config, OWWClient debe usar defaults (16000/mono/2)."""
    captured: list = []
    server = await _capture_audio_start_server(captured)
    port = server.sockets[0].getsockname()[1]

    async with server:
        client = OWWClient(OWWConfig(host="127.0.0.1", port=port))
        await client.connect()

    assert len(captured) == 1, f"Esperaba 1 mensaje audio-start, recibí {len(captured)}"
    audio_data = captured[0].get("data", {})
    assert audio_data.get("rate") == 16000, (
        f"audio-start rate esperado 16000 (default sin AudioConfig), obtuve {audio_data.get('rate')}"
    )
    assert audio_data.get("channels") == 1, (
        f"audio-start channels esperado 1 (default sin AudioConfig), obtuve {audio_data.get('channels')}"
    )
    assert audio_data.get("width") == 2, (
        f"audio-start width esperado 2 (PCM int16 del protocolo Wyoming), obtuve {audio_data.get('width')}"
    )


def test_audio_start_refleja_rate_y_channels_de_audio_config() -> None:
    asyncio.run(_test_audio_start_refleja_rate_y_channels_de_audio_config())


async def _test_audio_start_refleja_rate_y_channels_personalizados() -> None:
    """Con audio_config(sample_rate=48000, channels=2), OWWClient debe
    reflejar esos valores en audio-start (no los hardcodeados 16000/1)."""
    captured: list = []
    server = await _capture_audio_start_server(captured)
    port = server.sockets[0].getsockname()[1]

    audio_cfg = AudioConfig(sample_rate=48000, channels=2)
    async with server:
        client = OWWClient(OWWConfig(host="127.0.0.1", port=port), audio_cfg=audio_cfg)
        await client.connect()

    assert len(captured) == 1, f"Esperaba 1 mensaje audio-start, recibí {len(captured)}"
    audio_data = captured[0].get("data", {})
    assert audio_data.get("rate") == 48000, (
        f"audio-start rate esperaba 48000 (de AudioConfig), obtuve {audio_data.get('rate')} "
        f"(bug #14: hardcodeado a 16000)"
    )
    assert audio_data.get("channels") == 2, (
        f"audio-start channels esperaba 2 (de AudioConfig), obtuve {audio_data.get('channels')} "
        f"(bug #14: hardcodeado a 1)"
    )
    # width sigue siendo 2 (PCM int16) — constante del protocolo, no de AudioConfig
    assert audio_data.get("width") == 2


def test_audio_start_refleja_rate_y_channels_personalizados() -> None:
    asyncio.run(_test_audio_start_refleja_rate_y_channels_personalizados())


async def _test_audio_chunk_refleja_rate_y_channels_de_audio_config() -> None:
    """send_audio debe emitir el header audio-chunk con rate/channels de
    AudioConfig, no los hardcodeados 16000/1."""
    captured: list = []
    server = await _capture_audio_chunk_server(captured)
    port = server.sockets[0].getsockname()[1]

    audio_cfg = AudioConfig(sample_rate=48000, channels=2)
    async with server:
        client = OWWClient(OWWConfig(host="127.0.0.1", port=port), audio_cfg=audio_cfg)
        await client.connect()
        await client.send_audio(b"\x00\x01\x02\x03")

    assert len(captured) == 1, f"Esperaba 1 audio-chunk, recibí {len(captured)}"
    chunk_msg, payload = captured[0]
    assert chunk_msg.get("type") == "audio-chunk"
    audio_data = chunk_msg.get("data", {})
    assert audio_data.get("rate") == 48000, (
        f"audio-chunk rate esperaba 48000 (de AudioConfig), obtuve {audio_data.get('rate')} "
        f"(bug #14: hardcodeado a 16000)"
    )
    assert audio_data.get("channels") == 2, (
        f"audio-chunk channels esperaba 2 (de AudioConfig), obtuve {audio_data.get('channels')} "
        f"(bug #14: hardcodeado a 1)"
    )
    assert payload == b"\x00\x01\x02\x03"


def test_audio_chunk_refleja_rate_y_channels_de_audio_config() -> None:
    asyncio.run(_test_audio_chunk_refleja_rate_y_channels_de_audio_config())


async def _test_run_forever_recupera_connection_error_de_send_task(caplog) -> None:
    client = OWWClient(OWWConfig(reconnect_backoff_s=[0.0]))
    send_finished = asyncio.Event()

    async def _fail_sending(_audio) -> None:
        send_finished.set()
        raise ConnectionError("fallo de envío")

    async def _fail_receiving() -> str:
        await send_finished.wait()
        await asyncio.sleep(0)
        raise ConnectionError("fallo de recepción")

    client.connect_with_backoff = AsyncMock(
        side_effect=[None, asyncio.CancelledError()]
    )
    client._send_audio_loop = AsyncMock(side_effect=_fail_sending)
    client.wait_for_detection = AsyncMock(side_effect=_fail_receiving)
    client.disconnect = AsyncMock()

    async def _on_wake(_name: str) -> None:
        pass

    with caplog.at_level(logging.WARNING, logger="backends.oww_client"):
        try:
            await client.run_forever(_EmptyAudio(), _on_wake)
        except asyncio.CancelledError:
            pass

    assert client.connect_with_backoff.await_count == 2
    client.disconnect.assert_awaited_once()
    assert (
        "OWW send_task terminó con fallo de envío, continuando reconexión"
        in caplog.text
    )


def test_run_forever_recupera_connection_error_de_send_task(caplog) -> None:
    asyncio.run(_test_run_forever_recupera_connection_error_de_send_task(caplog))


# ---------------------------------------------------------------------------
# Issue #17 (fix secundario): wait_for_detection() debe propagar
# UnicodeDecodeError y asyncio.LimitOverrunError igual que el resto de
# excepciones de protocolo, en vez de dejar que escapen y maten
# run_forever() sin reconexión ordenada. Sin este catch, una línea
# corrupta o un mensaje >64KB del servidor mata la detección de wake
# word sin disconnect(), sin backoff, sin log.
# ---------------------------------------------------------------------------


class _FakeReader:
    """StreamReader mínimo para ejercitar el decode/parsing de wait_for_detection."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)
        self._closed = False

    async def readline(self) -> bytes:
        if not self._lines:
            raise asyncio.IncompleteReadError(b"", 0)
        return self._lines.pop(0)

    async def readexactly(self, n: int) -> bytes:
        return b""

    def at_eof(self) -> bool:
        return self._closed


async def _test_wait_for_detection_propaga_unicode_decode_error() -> None:
    """Si el servidor OWW emite bytes inválidos (p.ej. un proxy metiendo
    basura), line.decode() lanza UnicodeDecodeError — antes del fix de
    #17 escapaba del except (OSError, IncompleteReadError, ConnectionError)
    y mataba run_forever() sin marcar _connected=False."""
    client = OWWClient(OWWConfig())
    # Línea con bytes no-UTF-8 válidos: 0x80 solo no es un code point UTF-8
    client._reader = _FakeReader([b"\x80\xff\xfe garbage\n"])  # type: ignore[assignment]
    try:
        await client.wait_for_detection()
    except UnicodeDecodeError:
        pass  # propagación esperada: ahora se captura en el except general
    else:
        raise AssertionError(
            "wait_for_detection() debería propagar UnicodeDecodeError "
            "(ahora en el except junto a ConnectionError/OSError)"
        )
    assert client._connected is False, (
        "Tras propagar, _connected debe quedar False para que el bucle "
        "externo de run_forever detecte el cierre y haga backoff"
    )


async def _test_wait_for_detection_propaga_limit_overrun_error() -> None:
    """StreamReader.readline() puede lanzar asyncio.LimitOverrunError si
    una línea excede el buffer — subclase de Exception, no OSError, así
    que también escapaba antes del fix. Ahora se incluye en el except."""
    client = OWWClient(OWWConfig())

    class _OverflowReader(_FakeReader):
        async def readline(self) -> bytes:  # type: ignore[override]
            raise asyncio.LimitOverrunError("line too long", 65536)

    client._reader = _OverflowReader([])  # type: ignore[assignment]
    try:
        await client.wait_for_detection()
    except asyncio.LimitOverrunError:
        pass
    else:
        raise AssertionError(
            "wait_for_detection() debería propagar LimitOverrunError "
            "(ahora en el except junto a ConnectionError/OSError)"
        )
    assert client._connected is False


def test_wait_for_detection_propaga_unicode_decode_error() -> None:
    asyncio.run(_test_wait_for_detection_propaga_unicode_decode_error())


def test_wait_for_detection_propaga_limit_overrun_error() -> None:
    asyncio.run(_test_wait_for_detection_propaga_limit_overrun_error())
