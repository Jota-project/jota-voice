"""Tests de OWWClient — usan un servidor TCP real (no mocks) para el protocolo Wyoming."""
from __future__ import annotations

import asyncio
import json

from config import OWWConfig
from backends.oww_client import OWWClient


class _EmptyAudio:
    """AudioBackend fake: la cola nunca produce frames, _send_audio_loop se queda esperando."""

    def get_queue(self) -> asyncio.Queue:
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
