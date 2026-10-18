"""Tests de OWWClient — usan un servidor TCP real (no mocks) para el protocolo Wyoming."""
from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, patch

from config import AudioConfig, OWWConfig
from backends.oww_client import OWWClient


class _EmptyAudio:
    """AudioBackend fake: la cola nunca produce frames, _send_audio_loop se queda esperando."""

    def get_queue(self) -> asyncio.Queue:
        return asyncio.Queue()

    def get_oww_queue(self) -> asyncio.Queue:
        return asyncio.Queue()


async def _test_run_forever_aplica_backoff_tras_desconexion_post_conexion() -> None:
    """Si OWW cierra la conexión justo tras conectar (no un fallo de connect()
    inicial), run_forever debe esperar el backoff configurado antes de
    reconectar — no debe entrar en un hot-loop de reconexión sin límite.

    Issue #76: antes medía el tiempo real entre desconexiones (>= 0.35s
    dentro de una ventana de 2s), frágil con la CPU cargada. Ahora
    asyncio.sleep se sustituye por uno que registra el delay pedido y cede
    sin esperar: se comprueba el backoff solicitado, no el reloj."""
    disconnects: list = []
    three_disconnects = asyncio.Event()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readline()  # detect
        await reader.readline()  # audio-start
        disconnects.append(len(requested_sleeps))
        if len(disconnects) >= 3:
            three_disconnects.set()
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    real_sleep = asyncio.sleep
    requested_sleeps: list = []

    async def fake_sleep(delay: float, *args, **kwargs):
        requested_sleeps.append(delay)
        await real_sleep(0)

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async with server:
        cfg = OWWConfig(
            host="127.0.0.1",
            port=port,
            wake_words=["ok_nabu"],
            reconnect_backoff_s=[0.2, 0.3],
        )
        client = OWWClient(cfg)

        async def _on_wake(_name: str) -> None:
            pass

        with patch.object(asyncio, "sleep", fake_sleep):
            task = asyncio.create_task(client.run_forever(_EmptyAudio(), _on_wake))
            try:
                await asyncio.wait_for(three_disconnects.wait(), timeout=5.0)
            finally:
                task.cancel()
                await asyncio.wait([task], timeout=5.0)

    # Cada reconexión va precedida de su backoff: antes de la 2ª conexión se
    # pidió 1 sleep y antes de la 3ª, 2. Sin backoff (hot-loop) serían 0.
    assert disconnects[:3] == [0, 1, 2], (
        f"Reconectó sin backoff tras la desconexión post-conexión: "
        f"sleeps pedidos antes de cada conexión = {disconnects[:3]}"
    )
    # Escalera de backoff respetada y capada al último valor.
    assert requested_sleeps[:2] == [0.2, 0.3], requested_sleeps


def test_run_forever_aplica_backoff_tras_desconexion_post_conexion() -> None:
    asyncio.run(_test_run_forever_aplica_backoff_tras_desconexion_post_conexion())


async def _detection_burst_server(name: str, n_events: int) -> asyncio.base_events.Server:
    """Servidor Wyoming fake: completa el handshake, envía ``n_events``
    eventos 'detection' de golpe y cierra — simula el comportamiento real
    de wyoming-openwakeword, que dispara Detection en cada chunk de audio
    por encima del threshold mientras dura la locución, no una vez por
    locución."""

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readline()  # detect
        await reader.readline()  # audio-start

        line = json.dumps({"type": "detection", "data": {"name": name}}).encode() + b"\n"
        for _ in range(n_events):
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
    otra tras la ventana de debounce.

    Issue #76: antes dependía de un gap real de 0.2s en el servidor y de un
    sleep(0.4) fijo. Ahora el reloj del debounce es falso: cada detección
    consulta _now() una vez y recibe el siguiente instante del guion, así que
    el resultado no depende de cuándo llegan los bytes."""
    # 5 detecciones de la ráfaga (dentro de 0.1s) + 1 locución nueva a 0.25s.
    script = [0.0, 0.01, 0.02, 0.03, 0.099, 0.25]
    server = await _detection_burst_server("ok_jota", n_events=len(script))
    port = server.sockets[0].getsockname()[1]

    async with server:
        cfg = OWWConfig(
            host="127.0.0.1",
            port=port,
            wake_words=["ok_jota"],
            debounce_s=0.1,
        )
        client = OWWClient(cfg)
        clock = iter(script)
        accepted_at: list = []

        def _fake_now() -> float:
            return next(clock)

        client._now = _fake_now  # type: ignore[method-assign]
        await client.connect()

        detections: list = []

        async def _collect() -> None:
            try:
                while True:
                    name = await client.wait_for_detection()
                    detections.append(name)
                    accepted_at.append(client._last_trigger_at[name])
            except ConnectionError:
                pass  # el servidor fake cierra tras la ráfaga — esperado

        # Termina sola cuando el servidor cierra; el timeout es solo red de
        # seguridad.
        await asyncio.wait_for(_collect(), timeout=5.0)
        await client.disconnect()

    assert detections == ["ok_jota", "ok_jota"], (
        f"Esperaba 2 detecciones (ráfaga colapsada + 2ª tras el debounce), "
        f"hubo {len(detections)}: {detections}"
    )
    assert accepted_at == [0.0, 0.25]


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
        await client.disconnect()

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
        await client.disconnect()

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
        await client.disconnect()

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


# ---------------------------------------------------------------------------
# Issue #76: run_forever se tragaba la cancelación externa si llegaba
# mientras esperaba a que send_task terminase de cancelarse. El
# ``except asyncio.CancelledError: pass`` no distinguía la CancelledError
# de send_task de la de la propia task de run_forever, así que un
# ``task.cancel()`` (shutdown) o un ``asyncio.wait_for`` (tests) se perdía y
# el bucle seguía reconectando para siempre. Era la causa del cuelgue
# intermitente de test_oww_healthcheck.py.
# ---------------------------------------------------------------------------


async def _test_run_forever_propaga_cancelacion_durante_limpieza_de_send_task() -> None:
    client = OWWClient(OWWConfig(reconnect_backoff_s=[0.0]))
    send_started = asyncio.Event()
    send_cleaning_up = asyncio.Event()
    release_cleanup = asyncio.Event()
    calls = {"wfd": 0}

    async def _slow_cancel_send(_audio) -> None:
        send_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Limpieza que tarda: run_forever queda suspendido en
            # ``await send_task`` justo cuando llega la cancelación externa.
            send_cleaning_up.set()
            await release_cleanup.wait()
            raise

    async def _drop_then_block() -> str:
        calls["wfd"] += 1
        if calls["wfd"] == 1:
            await send_started.wait()
            raise ConnectionError("drop")
        await asyncio.Event().wait()  # si el bug sigue, el bucle llega aquí
        return "unreachable"  # pragma: no cover

    client.connect_with_backoff = AsyncMock(return_value=None)
    client._send_audio_loop = AsyncMock(side_effect=_slow_cancel_send)
    client.wait_for_detection = AsyncMock(side_effect=_drop_then_block)
    client.disconnect = AsyncMock()

    async def _on_wake(_name: str) -> None:
        pass

    task = asyncio.create_task(client.run_forever(_EmptyAudio(), _on_wake))
    try:
        await asyncio.wait_for(send_cleaning_up.wait(), timeout=5.0)
        task.cancel()
        release_cleanup.set()
        done, _ = await asyncio.wait([task], timeout=5.0)
        assert task in done, (
            "run_forever se tragó la cancelación externa recibida durante la "
            "limpieza de send_task y siguió reconectando"
        )
        assert task.cancelled(), "run_forever debe terminar cancelada"
        assert client.connect_with_backoff.await_count == 1, (
            "tras cancelar no debe volver a reconectar"
        )
    finally:
        if not task.done():
            task.cancel()
            await asyncio.wait([task], timeout=5.0)


def test_run_forever_propaga_cancelacion_durante_limpieza_de_send_task() -> None:
    asyncio.run(_test_run_forever_propaga_cancelacion_durante_limpieza_de_send_task())
