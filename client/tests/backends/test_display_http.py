"""Tests de HttpDisplayBackend — compatibilidad con el kiosk de jota-display.

El kiosk (jota-display/server/server.py) guarda tal cual el `state` que
recibe por POST /state y solo entiende idle/listening/thinking/response.
Estos tests fijan ese contrato y el hardening de las issues #58, #59 y #64.
No hay HTTP real: se sustituye ``_do_post`` (lo que corre en el hilo).
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from backends.display_http import HttpDisplayBackend
from backends.errors import ConfigError
from config import DEFAULT_DISPLAY_HTTP_URL, DisplayConfig

from tests.fakes.waiting import wait_until


def _backend(**kw) -> tuple[HttpDisplayBackend, list[dict]]:
    posts: list[dict] = []
    backend = HttpDisplayBackend(DisplayConfig(backend="http", **kw))
    backend._do_post = lambda payload: posts.append(payload)  # type: ignore[method-assign]
    return backend, posts


# ---------------------------------------------------------------------------
# Issue #58: URL
# ---------------------------------------------------------------------------


def test_url_vacia_usa_el_default_unico() -> None:
    backend = HttpDisplayBackend(DisplayConfig(backend="http", url=""))
    assert backend._url == DEFAULT_DISPLAY_HTTP_URL + "/state"


def test_url_con_barra_final() -> None:
    backend = HttpDisplayBackend(DisplayConfig(url="http://kiosk.local:8766/"))
    assert backend._url == "http://kiosk.local:8766/state"


@pytest.mark.parametrize("url", ["127.0.0.1:8766", "/state", "ftp://x/", "http://"])
def test_url_invalida_es_configerror(url: str) -> None:
    with pytest.raises(ConfigError, match="display.url"):
        HttpDisplayBackend(DisplayConfig(url=url))


# ---------------------------------------------------------------------------
# Contrato del kiosk: solo estados que entiende
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_estados_del_kiosk_se_postean_en_orden() -> None:
    backend, posts = _backend()
    await backend.start()
    try:
        backend.send("state", {"value": "listening", "text": ""})
        backend.send("state", {"value": "thinking", "text": "hola"})
        backend.send("state", {"value": "response", "text": ""})
        backend.send("state", {"value": "idle", "text": ""})
        await wait_until(lambda: len(posts) == 4, message="4 posts")
    finally:
        await backend.stop()
    assert posts == [
        {"state": "listening", "text": ""},
        {"state": "thinking", "text": "hola"},
        {"state": "response", "text": ""},
        {"state": "idle", "text": ""},
    ]


@pytest.mark.asyncio
async def test_mensajes_que_el_kiosk_no_entiende_no_se_postean() -> None:
    """Un `state` desconocido pisaría STATE["state"] en el kiosk y rompería
    su auto-apagado de pantalla (schedule_sleep solo mira "response")."""
    backend, posts = _backend()
    await backend.start()
    try:
        backend.send("turn_start", {"turn_id": "t-1"})
        backend.send("tool_call", {"phase": "start", "name": "exec"})
        backend.send("error", {"code": "X", "message": "boom", "fatal": False})
        backend.send("cancelled", {"state": "RESPONDING"})
        backend.send("health", {"key": "oww", "level": "warning"})
        backend.send("state", {"value": "error", "text": ""})
        backend.send("state", {"value": "idle", "text": ""})
        # La cola es FIFO y el idle es lo último que se envió: cuando llega
        # su POST, lo anterior ya se descartó (sin sleep extra, issue #76).
        await wait_until(lambda: len(posts) == 1, message="solo el idle")
    finally:
        await backend.stop()
    assert posts == [{"state": "idle", "text": ""}]


@pytest.mark.asyncio
async def test_text_update_se_traduce_a_response_con_texto_acumulado() -> None:
    backend, posts = _backend()
    await backend.start()
    try:
        backend.send("text_update", {
            "chunk_text": "Hola", "cumulative_text": "Hola", "duration_s": 0.0,
        })
        await wait_until(lambda: len(posts) == 1, message="post")
    finally:
        await backend.stop()
    assert posts == [{"state": "response", "text": "Hola"}]


@pytest.mark.asyncio
async def test_text_update_anima_el_reveal_durante_el_chunk() -> None:
    """El kiosk antes recibía el texto a trozos (ticks de 50 ms desde
    PlaybackEngine); ahora llega un mensaje por chunk y el backend http
    reproduce ese reveal localmente para no perder la sincronía visual."""
    backend, posts = _backend()
    backend._tick_s = 0.01
    await backend.start()
    try:
        backend.send("text_update", {
            "chunk_text": "ABCDEFGHIJ", "cumulative_text": "ABCDEFGHIJ", "duration_s": 0.05,
        })
        await wait_until(
            lambda: posts and posts[-1]["text"] == "ABCDEFGHIJ", message="reveal completo"
        )
    finally:
        await backend.stop()
    texts = [p["text"] for p in posts]
    assert len(texts) > 1
    assert all(p["state"] == "response" for p in posts)
    assert texts == sorted(texts, key=len)  # crece monótonamente
    assert texts[0] != "ABCDEFGHIJ"


# ---------------------------------------------------------------------------
# Issue #64: backlog acotado con un display lento
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_no_bloquea_y_coalesce_text_updates_con_display_lento() -> None:
    gate = threading.Event()
    entered = threading.Event()
    posts: list[dict] = []

    def _slow_post(payload: dict) -> None:
        entered.set()
        gate.wait(timeout=5)
        posts.append(payload)

    backend = HttpDisplayBackend(DisplayConfig(backend="http", timeout_s=5.0))
    backend._do_post = _slow_post  # type: ignore[method-assign]
    await backend.start()
    try:
        backend.send("state", {"value": "response", "text": ""})
        # El sender ya está bloqueado en el POST (antes: sleep(0.02), #76).
        await wait_until(entered.is_set, message="sender bloqueado en el POST")
        cumulative = ""
        for i in range(500):
            cumulative += f"{i} "
            backend.send("text_update", {
                "chunk_text": f"{i} ", "cumulative_text": cumulative, "duration_s": 0.0,
            })
        # send() es síncrono: 500 llamadas no esperan al display, y la cola
        # no crece con ellas (los text_update consecutivos se fusionan).
        assert len(backend._pending) <= 2
        gate.set()
        await wait_until(
            lambda: posts and posts[-1]["text"] == cumulative, message="último texto"
        )
    finally:
        gate.set()
        await backend.stop()
    assert len(posts) <= 3


@pytest.mark.asyncio
async def test_cola_acotada_descarta_lo_mas_antiguo() -> None:
    gate = threading.Event()
    entered = threading.Event()

    def _blocked_post(payload: dict) -> None:
        entered.set()
        gate.wait(timeout=5)

    backend = HttpDisplayBackend(DisplayConfig(backend="http", timeout_s=5.0))
    backend._do_post = _blocked_post  # type: ignore[method-assign]
    await backend.start()
    try:
        backend.send("state", {"value": "idle", "text": ""})
        await wait_until(entered.is_set, message="sender bloqueado en el POST")
        for i in range(200):
            backend.send("state", {"value": "listening" if i % 2 else "thinking", "text": str(i)})
        assert len(backend._pending) == backend.MAX_PENDING
        assert backend._pending[-1].payload["text"] == "199"
    finally:
        gate.set()
        await backend.stop()


# ---------------------------------------------------------------------------
# Issue #59: un POST colgado (p.ej. en la resolución DNS) no bloquea nada
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_colgado_no_bloquea_el_executor_por_defecto_ni_el_sender() -> None:
    stuck = threading.Event()
    calls: list[dict] = []

    def _hanging_post(payload: dict) -> None:
        calls.append(payload)
        stuck.wait(timeout=10)  # simula getaddrinfo sin timeout

    backend = HttpDisplayBackend(DisplayConfig(backend="http", timeout_s=0.05))
    backend._do_post = _hanging_post  # type: ignore[method-assign]
    await backend.start()
    try:
        backend.send("state", {"value": "listening", "text": ""})
        await wait_until(lambda: len(calls) == 1, message="primer POST")
        # El executor por defecto del loop (compartido con el resto de la
        # app) sigue libre: el POST corre en un executor propio.
        loop = asyncio.get_running_loop()
        assert await asyncio.wait_for(loop.run_in_executor(None, lambda: 42), 1.0) == 42
        # El sender no se queda esperando al hilo colgado: tras timeout_s
        # sigue procesando la cola (y descarta mientras el hilo siga colgado,
        # en vez de apilar trabajos detrás de él).
        backend.send("state", {"value": "idle", "text": ""})
        await wait_until(lambda: not backend._pending, message="cola drenada")
        assert len(calls) == 1
    finally:
        stuck.set()
        await backend.stop()


@pytest.mark.asyncio
async def test_post_real_contra_un_servidor_como_el_kiosk() -> None:
    """Camino real (urllib en el executor propio) contra un servidor HTTP
    local que imita POST /state de jota-display/server/server.py."""
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    received: list[dict] = []

    class _Kiosk(BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            received.append({"path": self.path, "ctype": self.headers.get("Content-Type"),
                             "body": json.loads(body)})
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Kiosk)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    backend = HttpDisplayBackend(DisplayConfig(url=f"http://127.0.0.1:{srv.server_address[1]}"))
    await backend.start()
    try:
        backend.send("state", {"value": "thinking", "text": "hola"})
        await wait_until(lambda: len(received) == 1, message="POST recibido")
    finally:
        await backend.stop()
        srv.shutdown()
        srv.server_close()
    assert received == [{"path": "/state", "ctype": "application/json",
                         "body": {"state": "thinking", "text": "hola"}}]


@pytest.mark.asyncio
async def test_error_de_red_no_propaga() -> None:
    calls: list[dict] = []

    def _boom(payload: dict) -> None:
        calls.append(payload)
        raise OSError("connection refused")

    backend = HttpDisplayBackend(DisplayConfig(backend="http"))
    backend._do_post = _boom  # type: ignore[method-assign]
    await backend.start()
    try:
        backend.send("state", {"value": "idle", "text": ""})
        await wait_until(lambda: len(calls) == 1, message="primer POST")
        # Prueba positiva de que el sender sigue vivo tras el error: procesa
        # el siguiente mensaje (antes: sleep(0.02) y mirar task.done(), #76).
        backend.send("state", {"value": "listening", "text": ""})
        await wait_until(lambda: len(calls) == 2, message="el sender sobrevivió al error")
        assert backend._task is not None and not backend._task.done()
    finally:
        await backend.stop()
