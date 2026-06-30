# Cancel Signal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Añadir `{"type":"cancel"}` al protocolo gateway-cliente, con un servidor HTTP interno en `localhost:8765` que jota-display puede llamar para cancelar el turn activo durante RECORDING o RESPONDING.

**Architecture:** Un `asyncio.Event` compartido (`cancel_event`) conecta el servidor de control HTTP con la state machine. El servidor activa el evento al recibir `POST /cancel`; la state machine hace race entre su tarea principal y `cancel_event.wait()` usando `asyncio.wait(FIRST_COMPLETED)`. Si cancel gana, envía `{"type":"cancel"}` al gateway y lanza `_TurnCancelled` para volver a IDLE.

**Tech Stack:** Python ≥ 3.10, asyncio stdlib (sin aiohttp), pytest, websockets, pyyaml.

## Global Constraints

- Sin dependencias nuevas — el control_server usa únicamente `asyncio` stdlib.
- Tests en `client/` junto al código fuente, compatibles con pytest.
- Ejecutar tests desde la raíz del repo: `python -m pytest client/test_<archivo>.py -v`
- El parámetro `cancel_event` en `state_machine.run()` tiene default `None` para no romper tests existentes.
- El servidor de control falla silenciosamente si el puerto está ocupado (warning en log, no crash).

---

### Task 1: `send_cancel()` en GatewayClient

**Files:**
- Modify: `client/gateway_client.py`
- Create: `client/test_gateway_client.py`

**Interfaces:**
- Produces: `GatewayClient.send_cancel() -> None` — envía `{"type":"cancel"}` por WebSocket. Lanza `RuntimeError("GatewayClient: no conectado")` si `_ws is None`.

---

- [ ] **Step 1: Crear test_gateway_client.py con tests que fallan**

Crear `client/test_gateway_client.py`:

```python
"""Tests de GatewayClient — no requieren servidor real."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

# --- Stubs ---
if "websockets" not in sys.modules:
    stub = types.ModuleType("websockets")
    exc_stub = types.ModuleType("websockets.exceptions")
    class ConnectionClosed(Exception): pass
    exc_stub.ConnectionClosed = ConnectionClosed
    stub.exceptions = exc_stub
    stub.connect = AsyncMock()
    sys.modules["websockets"] = stub
    sys.modules["websockets.exceptions"] = exc_stub

_here = os.path.dirname(__file__)
if _here not in sys.path:
    sys.path.insert(0, _here)

from config import GatewayConfig
from gateway_client import GatewayClient


async def _test_send_cancel_envia_mensaje() -> None:
    cfg = GatewayConfig(host="127.0.0.1", client_key="test")
    client = GatewayClient(cfg)
    ws_mock = MagicMock()
    ws_mock.send = AsyncMock()
    client._ws = ws_mock

    await client.send_cancel()

    ws_mock.send.assert_awaited_once()
    sent = json.loads(ws_mock.send.call_args[0][0])
    assert sent == {"type": "cancel"}, f"Esperaba {{\"type\":\"cancel\"}}, got {sent}"


async def _test_send_cancel_sin_ws_lanza_error() -> None:
    cfg = GatewayConfig(host="127.0.0.1", client_key="test")
    client = GatewayClient(cfg)  # _ws = None
    try:
        await client.send_cancel()
        raise AssertionError("Debería haber lanzado RuntimeError")
    except RuntimeError as exc:
        assert "no conectado" in str(exc)


def test_send_cancel_envia_mensaje() -> None:
    asyncio.run(_test_send_cancel_envia_mensaje())


def test_send_cancel_sin_ws_lanza_error() -> None:
    asyncio.run(_test_send_cancel_sin_ws_lanza_error())


if __name__ == "__main__":
    asyncio.run(_test_send_cancel_envia_mensaje())
    asyncio.run(_test_send_cancel_sin_ws_lanza_error())
    print("=== TODOS LOS TESTS PASARON ===")
```

- [ ] **Step 2: Ejecutar — verificar que fallan**

```
python -m pytest client/test_gateway_client.py -v
```

Resultado esperado: `AttributeError: 'GatewayClient' object has no attribute 'send_cancel'`

- [ ] **Step 3: Añadir `send_cancel()` a gateway_client.py**

En `client/gateway_client.py`, después del método `send_end()` (línea ~59), añadir:

```python
    async def send_cancel(self) -> None:
        if self._ws is None:
            raise RuntimeError("GatewayClient: no conectado")
        await self._ws.send(json.dumps({"type": "cancel"}))
        log.debug("Gateway: enviado cancel")
```

- [ ] **Step 4: Ejecutar — verificar que pasan**

```
python -m pytest client/test_gateway_client.py -v
```

Resultado esperado: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add client/gateway_client.py client/test_gateway_client.py
git commit -m "feat(gateway): añadir send_cancel()"
```

---

### Task 2: `ControlConfig` en config.py

**Files:**
- Modify: `client/config.py`
- Modify: `config.example.yaml`
- Create: `client/test_config_control.py`

**Interfaces:**
- Produces: `ControlConfig(port: int = 8765)` dataclass. Disponible como `cfg.control` en `Config`.

---

- [ ] **Step 1: Crear test_config_control.py con tests que fallan**

Crear `client/test_config_control.py`:

```python
"""Tests de ControlConfig en config.py."""
from __future__ import annotations

import os
import sys
import tempfile

import yaml

_here = os.path.dirname(__file__)
if _here not in sys.path:
    sys.path.insert(0, _here)


def _write_cfg(data: dict) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    yaml.dump(data, f)
    f.close()
    return f.name


def test_control_port_default() -> None:
    path = _write_cfg({"gateway": {"host": "127.0.0.1", "client_key": "test"}})
    try:
        from config import load_config
        cfg = load_config(path)
        assert cfg.control.port == 8765, f"Esperaba 8765, got {cfg.control.port}"
    finally:
        os.unlink(path)


def test_control_port_custom() -> None:
    path = _write_cfg({
        "gateway": {"host": "127.0.0.1", "client_key": "test"},
        "control": {"port": 9000},
    })
    try:
        from config import load_config
        cfg = load_config(path)
        assert cfg.control.port == 9000, f"Esperaba 9000, got {cfg.control.port}"
    finally:
        os.unlink(path)


if __name__ == "__main__":
    test_control_port_default()
    test_control_port_custom()
    print("=== TODOS LOS TESTS PASARON ===")
```

- [ ] **Step 2: Ejecutar — verificar que fallan**

```
python -m pytest client/test_config_control.py -v
```

Resultado esperado: `AttributeError: 'Config' object has no attribute 'control'`

- [ ] **Step 3: Añadir ControlConfig a config.py**

En `client/config.py`:

a) Añadir dataclass después de `DisplayConfig` (~línea 47):

```python
@dataclass
class ControlConfig:
    port: int = 8765
```

b) Añadir `control` a `Config` (~línea 58):

```python
@dataclass
class Config:
    gateway: GatewayConfig
    oww: OWWConfig = field(default_factory=OWWConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
```

c) Añadir función `_control_from_dict` después de `_display_from_dict` (~línea 101):

```python
def _control_from_dict(d: dict) -> ControlConfig:
    return ControlConfig(
        port=int(d.get("port", 8765)),
    )
```

d) Actualizar `load_config` para pasar `control:` (~línea 109):

```python
    return Config(
        gateway=_gateway_from_dict(data["gateway"]),
        oww=_oww_from_dict(data.get("oww", {})),
        audio=_audio_from_dict(data.get("audio", {})),
        display=_display_from_dict(data.get("display", {})),
        control=_control_from_dict(data.get("control", {})),
        logging=LoggingConfig(level=data.get("logging", {}).get("level", "INFO")),
    )
```

- [ ] **Step 4: Actualizar config.example.yaml**

Añadir al final de `config.example.yaml`:

```yaml

control:
  port: 8765                  # Puerto del servidor de control HTTP (para botón cancel)
```

- [ ] **Step 5: Ejecutar — verificar que pasan**

```
python -m pytest client/test_config_control.py -v
```

Resultado esperado: `2 passed`

- [ ] **Step 6: Commit**

```bash
git add client/config.py config.example.yaml client/test_config_control.py
git commit -m "feat(config): añadir ControlConfig con port=8765"
```

---

### Task 3: `control_server.py`

**Files:**
- Create: `client/control_server.py`
- Create: `client/test_control_server.py`

**Interfaces:**
- Consumes: `ControlConfig` de Task 2, `asyncio.Event` como `cancel_event`.
- Produces: `async def run(cfg: ControlConfig, cancel_event: asyncio.Event) -> None` — arranca el servidor y sirve indefinidamente (hasta cancelación de la task asyncio). Si el puerto está ocupado, loguea warning y retorna (no crash).

---

- [ ] **Step 1: Crear test_control_server.py con tests que fallan**

Crear `client/test_control_server.py`:

```python
"""Tests del servidor HTTP de control."""
from __future__ import annotations

import asyncio
import os
import sys

_here = os.path.dirname(__file__)
if _here not in sys.path:
    sys.path.insert(0, _here)

from config import ControlConfig


async def _test_post_cancel_activa_evento() -> None:
    import control_server

    cancel_event = asyncio.Event()
    cfg = ControlConfig(port=18765)

    server_task = asyncio.create_task(control_server.run(cfg, cancel_event))
    await asyncio.sleep(0.05)

    reader, writer = await asyncio.open_connection("127.0.0.1", 18765)
    writer.write(
        b"POST /cancel HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Content-Length: 0\r\n"
        b"\r\n"
    )
    await writer.drain()
    response = await asyncio.wait_for(reader.read(1024), timeout=2.0)
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass

    assert b"200" in response, f"Esperaba 200, got: {response[:100]!r}"
    assert cancel_event.is_set(), "cancel_event debería estar activado"

    server_task.cancel()
    try:
        await server_task
    except asyncio.CancelledError:
        pass


async def _test_endpoint_desconocido_retorna_404() -> None:
    import control_server

    cancel_event = asyncio.Event()
    cfg = ControlConfig(port=18766)

    server_task = asyncio.create_task(control_server.run(cfg, cancel_event))
    await asyncio.sleep(0.05)

    reader, writer = await asyncio.open_connection("127.0.0.1", 18766)
    writer.write(b"GET /unknown HTTP/1.1\r\nHost: localhost\r\n\r\n")
    await writer.drain()
    response = await asyncio.wait_for(reader.read(1024), timeout=2.0)
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass

    assert b"404" in response, f"Esperaba 404, got: {response[:100]!r}"
    assert not cancel_event.is_set(), "cancel_event NO debería activarse"

    server_task.cancel()
    try:
        await server_task
    except asyncio.CancelledError:
        pass


async def _test_puerto_ocupado_no_crashea() -> None:
    import control_server

    # Ocupar el puerto manualmente
    blocker = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 18767)
    cancel_event = asyncio.Event()
    cfg = ControlConfig(port=18767)

    # Debe retornar sin excepción
    await asyncio.wait_for(control_server.run(cfg, cancel_event), timeout=2.0)

    blocker.close()
    await blocker.wait_closed()


def test_post_cancel_activa_evento() -> None:
    asyncio.run(_test_post_cancel_activa_evento())


def test_endpoint_desconocido_retorna_404() -> None:
    asyncio.run(_test_endpoint_desconocido_retorna_404())


def test_puerto_ocupado_no_crashea() -> None:
    asyncio.run(_test_puerto_ocupado_no_crashea())


if __name__ == "__main__":
    asyncio.run(_test_post_cancel_activa_evento())
    asyncio.run(_test_endpoint_desconocido_retorna_404())
    asyncio.run(_test_puerto_ocupado_no_crashea())
    print("=== TODOS LOS TESTS PASARON ===")
```

- [ ] **Step 2: Ejecutar — verificar que fallan**

```
python -m pytest client/test_control_server.py -v
```

Resultado esperado: `ModuleNotFoundError: No module named 'control_server'`

- [ ] **Step 3: Crear client/control_server.py**

Crear `client/control_server.py`:

```python
"""
control_server.py — Servidor HTTP de control de jota-voice.

Expone POST /cancel en localhost para que jota-display (u otros clientes)
puedan cancelar el turn activo. Usa asyncio puro, sin dependencias externas.
"""

from __future__ import annotations

import asyncio
import logging

from config import ControlConfig

log = logging.getLogger(__name__)


async def run(cfg: ControlConfig, cancel_event: asyncio.Event) -> None:
    """Arranca el servidor y sirve hasta que la task asyncio sea cancelada."""
    try:
        server = await asyncio.start_server(
            lambda r, w: _handle(r, w, cancel_event),
            host="127.0.0.1",
            port=cfg.port,
        )
    except OSError as exc:
        log.warning(
            "ControlServer: no se pudo arrancar en puerto %d: %s — cancel por botón desactivado",
            cfg.port,
            exc,
        )
        return

    addr = server.sockets[0].getsockname()
    log.info("ControlServer escuchando en %s:%d", addr[0], addr[1])
    async with server:
        await server.serve_forever()


async def _handle(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    cancel_event: asyncio.Event,
) -> None:
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        parts = request_line.decode(errors="replace").strip().split()
        method = parts[0] if len(parts) > 0 else ""
        path = parts[1] if len(parts) > 1 else ""

        # Leer headers hasta línea vacía
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if line in (b"\r\n", b"\n", b""):
                break

        if method == "POST" and path == "/cancel":
            cancel_event.set()
            log.info("ControlServer: cancel recibido")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
        else:
            writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")

        await writer.drain()
    except Exception as exc:
        log.debug("ControlServer: error en conexión: %s", exc)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
```

- [ ] **Step 4: Ejecutar — verificar que pasan**

```
python -m pytest client/test_control_server.py -v
```

Resultado esperado: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add client/control_server.py client/test_control_server.py
git commit -m "feat: añadir control_server HTTP para señal cancel"
```

---

### Task 4: Cancel en `state_machine.py`

**Files:**
- Modify: `client/state_machine.py`
- Modify: `client/test_state_machine.py` (añadir tests al final)

**Interfaces:**
- Consumes: `GatewayClient.send_cancel()` de Task 1.
- Produces:
  - `_TurnCancelled` — excepción privada, lanzada cuando cancel_event gana la race.
  - `_safe_send_cancel(gateway: GatewayClient) -> None` — helper async, llama send_cancel() ignorando errores.
  - `run(..., cancel_event: Optional[asyncio.Event] = None)` — parámetro nuevo con default None.

---

- [ ] **Step 1: Añadir tests de cancel a test_state_machine.py**

Al final de `client/test_state_machine.py`, antes de la sección `Entry points`, añadir:

```python
# ---------------------------------------------------------------------------
# Test 4: cancel durante RECORDING
# ---------------------------------------------------------------------------

async def _run_cancel_recording_test() -> None:
    print("\n=== Test cancel en RECORDING ===")

    bus = EventBus()
    cfg = _make_config()
    audio = _make_audio_mock(silent=False)  # audio con voz → no termina por silencio

    oww = _make_oww_mock()
    call_count = [0]

    async def _detect_once() -> str:
        call_count[0] += 1
        if call_count[0] == 1:
            return "ok_nabu"
        await asyncio.Event().wait()
        return "ok_nabu"

    oww.wait_for_detection = _detect_once

    gateway = MagicMock()
    gateway.connect = AsyncMock()
    gateway.disconnect = AsyncMock()
    gateway.send_audio = AsyncMock()
    gateway.send_end = AsyncMock()
    gateway.send_cancel = AsyncMock()
    gateway.receive = lambda: (x for x in [])  # never used

    playback = _make_playback_mock()

    cancel_event = asyncio.Event()
    received: list[VoiceEvent] = []
    stop_event = asyncio.Event()

    async def _collector() -> None:
        async for ev in bus.subscribe():
            received.append(ev)

    async def _watcher() -> None:
        idle_count = [0]
        async for ev in bus.subscribe():
            if ev.type == "state_changed" and ev.data.get("state") == "idle":
                idle_count[0] += 1
                if idle_count[0] >= 2:
                    stop_event.set()
                    return

    collector_task = asyncio.create_task(_collector())
    watcher_task = asyncio.create_task(_watcher())

    from state_machine import run as sm_run

    sm_task = asyncio.create_task(
        sm_run(cfg, bus, audio, oww, gateway, playback, cancel_event)
    )

    # Esperar a que empiece RECORDING y disparar cancel
    async def _fire_cancel() -> None:
        # Esperar recording_started
        while not any(e.type == "recording_started" for e in received):
            await asyncio.sleep(0.01)
        cancel_event.set()

    fire_task = asyncio.create_task(_fire_cancel())

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        pass

    sm_task.cancel()
    watcher_task.cancel()
    fire_task.cancel()
    for t in [sm_task, watcher_task, fire_task]:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass

    bus.close()
    try:
        await collector_task
    except Exception:
        pass

    event_types = [e.type for e in received]
    print(f"\nEventos: {event_types}")

    # recording_ended NO debe aparecer (el turn fue cancelado)
    assert "recording_ended" not in event_types, (
        f"recording_ended no debe publicarse en cancel: {event_types}"
    )
    # send_cancel debe haber sido llamado
    assert gateway.send_cancel.await_count >= 1, "gateway.send_cancel() no fue llamado"
    # send_end NO debe haber sido llamado
    assert gateway.send_end.await_count == 0, "gateway.send_end() no debe llamarse en cancel"
    # vuelve a IDLE (≥2 state_changed idle)
    idle_evs = [e for e in received if e.type == "state_changed" and e.data.get("state") == "idle"]
    assert len(idle_evs) >= 2, f"Esperaba ≥2 idle, got {len(idle_evs)}: {event_types}"

    print("  OK  recording_ended no publicado")
    print("  OK  send_cancel() llamado")
    print("  OK  send_end() no llamado")
    print("  OK  vuelve a IDLE")
    print("Test cancel en RECORDING: PASADO")


# ---------------------------------------------------------------------------
# Test 5: cancel durante RESPONDING
# ---------------------------------------------------------------------------

async def _run_cancel_responding_test() -> None:
    print("\n=== Test cancel en RESPONDING ===")

    bus = EventBus()
    cfg = _make_config()
    audio = _make_audio_mock(silent=True)

    oww = _make_oww_mock()
    call_count = [0]

    async def _detect_once() -> str:
        call_count[0] += 1
        if call_count[0] == 1:
            return "ok_nabu"
        await asyncio.Event().wait()
        return "ok_nabu"

    oww.wait_for_detection = _detect_once

    # Gateway que emite transcription y luego bloquea (tts lento)
    received_events: list[VoiceEvent] = []

    async def _slow_receive():
        yield GatewayEvent(type="transcription", data={"text": "¿qué hora es?"})
        yield GatewayEvent(type="llm_token", data={"content": "Son "})
        await asyncio.Event().wait()  # bloquea — simulando TTS lento

    gateway = MagicMock()
    gateway.connect = AsyncMock()
    gateway.disconnect = AsyncMock()
    gateway.send_audio = AsyncMock()
    gateway.send_end = AsyncMock()
    gateway.send_cancel = AsyncMock()
    gateway.send_text = AsyncMock()
    gateway.receive = _slow_receive

    playback = _make_playback_mock()
    cancel_event = asyncio.Event()
    received: list[VoiceEvent] = []  # noqa: F841 — usado por _watcher y _fire_cancel
    stop_event = asyncio.Event()

    async def _collector() -> None:
        async for ev in bus.subscribe():
            received.append(ev)

    async def _watcher() -> None:
        idle_count = [0]
        async for ev in bus.subscribe():
            if ev.type == "state_changed" and ev.data.get("state") == "idle":
                idle_count[0] += 1
                if idle_count[0] >= 2:
                    stop_event.set()
                    return

    collector_task = asyncio.create_task(_collector())
    watcher_task = asyncio.create_task(_watcher())

    from state_machine import run as sm_run

    sm_task = asyncio.create_task(
        sm_run(cfg, bus, audio, oww, gateway, playback, cancel_event)
    )

    # Disparar cancel cuando llegue llm_token (estamos en RESPONDING)
    async def _fire_cancel() -> None:
        while not any(e.type == "llm_token" for e in received):
            await asyncio.sleep(0.01)
        cancel_event.set()

    fire_task = asyncio.create_task(_fire_cancel())

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        pass

    sm_task.cancel()
    watcher_task.cancel()
    fire_task.cancel()
    for t in [sm_task, watcher_task, fire_task]:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass

    bus.close()
    try:
        await collector_task
    except Exception:
        pass

    event_types = [e.type for e in received]
    print(f"\nEventos: {event_types}")

    # playback_ended NO debe aparecer
    assert "playback_ended" not in event_types, (
        f"playback_ended no debe publicarse en cancel: {event_types}"
    )
    # send_cancel debe haber sido llamado
    assert gateway.send_cancel.await_count >= 1, "gateway.send_cancel() no fue llamado"
    # vuelve a IDLE
    idle_evs = [e for e in received if e.type == "state_changed" and e.data.get("state") == "idle"]
    assert len(idle_evs) >= 2, f"Esperaba ≥2 idle, got {len(idle_evs)}"

    print("  OK  playback_ended no publicado")
    print("  OK  send_cancel() llamado")
    print("  OK  vuelve a IDLE")
    print("Test cancel en RESPONDING: PASADO")
```

Y añadir al bloque `Entry points` (funciones pytest y `_run_all`):

```python
def test_state_machine_cancel_recording() -> None:
    asyncio.run(_run_cancel_recording_test())


def test_state_machine_cancel_responding() -> None:
    asyncio.run(_run_cancel_responding_test())
```

Y en `_run_all`:
```python
    await _run_cancel_recording_test()
    await _run_cancel_responding_test()
```

- [ ] **Step 2: Ejecutar — verificar que los tests nuevos fallan**

```
python -m pytest client/test_state_machine.py::test_state_machine_cancel_recording client/test_state_machine.py::test_state_machine_cancel_responding -v
```

Resultado esperado: `TypeError` — `run()` no acepta `cancel_event`.

- [ ] **Step 3: Modificar state_machine.py**

Cambios en `client/state_machine.py`:

**a) Añadir import Optional al inicio:**

```python
from typing import Optional
```

**b) Añadir `_TurnCancelled` y `_safe_send_cancel` en la sección "State helpers":**

```python
class _TurnCancelled(Exception):
    """Lanzada cuando cancel_event gana la race en RECORDING o RESPONDING."""


async def _safe_send_cancel(gateway: GatewayClient) -> None:
    try:
        await gateway.send_cancel()
    except Exception:
        pass
```

**c) Reemplazar `_recording()` completo:**

```python
async def _recording(
    wake_word: str,
    bus: EventBus,
    audio: AudioCapture,
    gateway: GatewayClient,
    playback: PlaybackEngine,
    cfg: Config,
    cancel_event: asyncio.Event,
) -> None:
    cancel_event.clear()

    bus.publish(VoiceEvent(type="wake_word_detected", data={"wake_word": wake_word}))
    bus.publish(VoiceEvent(type="recording_started", data={}))

    await asyncio.wait_for(
        gateway.connect(),
        timeout=cfg.gateway.connect_timeout_s,
    )
    log.debug("RECORDING: gateway conectado")

    preroll = audio.get_preroll()
    if preroll:
        await gateway.send_audio(preroll)
        log.debug("RECORDING: pre-roll enviado (%d bytes)", len(preroll))

    async def _capture_loop() -> None:
        q = audio.get_queue()
        silence_frames_needed = max(1, int(
            cfg.audio.silence_timeout_s * cfg.audio.sample_rate / cfg.audio.frames_per_buffer
        ))
        silence_count = 0
        loop = asyncio.get_running_loop()
        deadline = loop.time() + cfg.audio.recording_timeout_s

        while loop.time() < deadline:
            remaining = deadline - loop.time()
            try:
                frame = await asyncio.wait_for(q.get(), timeout=min(remaining, 0.1))
            except asyncio.TimeoutError:
                continue
            await gateway.send_audio(frame)
            if audio.is_silence(frame):
                silence_count += 1
                if silence_count >= silence_frames_needed:
                    log.info("RECORDING: fin por silencio (%d frames)", silence_count)
                    return
            else:
                silence_count = 0
        log.info("RECORDING: timeout absoluto alcanzado (%.1fs)", cfg.audio.recording_timeout_s)

    capture_task = asyncio.create_task(_capture_loop())
    cancel_task = asyncio.create_task(cancel_event.wait())

    done, pending = await asyncio.wait(
        [capture_task, cancel_task],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    if cancel_task in done:
        await _safe_send_cancel(gateway)
        raise _TurnCancelled()

    await playback.play_notification()
    await gateway.send_end()
    bus.publish(VoiceEvent(type="recording_ended", data={}))
    log.debug("RECORDING: end enviado")
```

**d) Reemplazar `_responding()` completo:**

```python
async def _responding(
    bus: EventBus,
    gateway: GatewayClient,
    playback: PlaybackEngine,
    cancel_event: asyncio.Event,
) -> None:
    playback_started = False

    async def _receive_loop() -> None:
        nonlocal playback_started
        async for gw_event in gateway.receive():
            if gw_event.type == "transcription":
                text = gw_event.data.get("text", "")
                bus.publish(VoiceEvent(type="transcription", data={"text": text}))
                log.info("RESPONDING: transcription → %r", text)
                await gateway.send_text(text)

            elif gw_event.type == "transcription_partial":
                text = gw_event.data.get("text", "")
                bus.publish(VoiceEvent(type="transcription_partial", data={"text": text}))

            elif gw_event.type == "llm_token":
                content = gw_event.data.get("content", "")
                playback.push_token(content)
                bus.publish(VoiceEvent(type="llm_token", data={"content": content}))

            elif gw_event.type == "tts_chunk":
                if not playback_started:
                    bus.publish(VoiceEvent(type="playback_started", data={}))
                    playback_started = True
                audio_bytes = gw_event.data.get("audio", b"")
                await playback.play_chunk(audio_bytes)

            else:
                log.debug("RESPONDING: evento desconocido de gateway: %r", gw_event.type)

    receive_task = asyncio.create_task(
        asyncio.wait_for(_receive_loop(), timeout=30.0)
    )
    cancel_task = asyncio.create_task(cancel_event.wait())

    done, pending = await asyncio.wait(
        [receive_task, cancel_task],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

    if cancel_task in done:
        await _safe_send_cancel(gateway)
        playback.reset()
        raise _TurnCancelled()

    if not receive_task.cancelled():
        exc = receive_task.exception()
        if exc is not None:
            if isinstance(exc, asyncio.TimeoutError):
                log.warning("RESPONDING: timeout 30s")
                bus.publish(VoiceEvent(type="error", data={"message": "Timeout en estado RESPONDING"}))
                return
            raise exc

    await playback.drain()
    bus.publish(VoiceEvent(type="playback_ended", data={}))
    log.debug("RESPONDING: reproducción completada")
```

**e) Actualizar `run()` — añadir `cancel_event` y manejar `_TurnCancelled`:**

```python
async def run(
    cfg: Config,
    bus: EventBus,
    audio: AudioCapture,
    oww: OWWClient,
    gateway: GatewayClient,
    playback: PlaybackEngine,
    cancel_event: Optional[asyncio.Event] = None,
) -> None:
    if cancel_event is None:
        cancel_event = asyncio.Event()

    log.info("StateMachine: iniciando loop")

    while True:
        state = "IDLE"
        try:
            wake_word = await _idle(cfg, bus, audio, oww)
        except asyncio.CancelledError:
            log.info("StateMachine: cancelado en IDLE")
            raise
        except Exception as exc:
            _log_error(state, exc, bus)
            await _cleanup(gateway, playback)
            continue

        state = "RECORDING"
        try:
            await _recording(wake_word, bus, audio, gateway, playback, cfg, cancel_event)
        except _TurnCancelled:
            log.info("StateMachine: turn cancelado en RECORDING")
            await _cleanup(gateway, playback)
            continue
        except asyncio.CancelledError:
            log.info("StateMachine: cancelado en RECORDING")
            await _cleanup(gateway, playback)
            raise
        except Exception as exc:
            _log_error(state, exc, bus)
            await _cleanup(gateway, playback)
            continue

        state = "RESPONDING"
        try:
            await _responding(bus, gateway, playback, cancel_event)
        except _TurnCancelled:
            log.info("StateMachine: turn cancelado en RESPONDING")
        except asyncio.CancelledError:
            log.info("StateMachine: cancelado en RESPONDING")
            raise
        except Exception as exc:
            _log_error(state, exc, bus)
        finally:
            await _cleanup(gateway, playback)
```

- [ ] **Step 4: Ejecutar todos los tests de state_machine**

```
python -m pytest client/test_state_machine.py -v
```

Resultado esperado: `5 passed` (3 originales + 2 nuevos)

- [ ] **Step 5: Commit**

```bash
git add client/state_machine.py client/test_state_machine.py
git commit -m "feat(state_machine): señal cancel con asyncio.Event y _TurnCancelled"
```

---

### Task 5: Wiring en `voice_client.py`

**Files:**
- Modify: `client/voice_client.py`

**Interfaces:**
- Consumes: `control_server.run(cfg: ControlConfig, cancel_event: asyncio.Event)` de Task 3. `state_machine.run(..., cancel_event)` de Task 4.

---

- [ ] **Step 1: Modificar voice_client.py**

En `client/voice_client.py`:

**a) Añadir imports** (junto a los otros imports de módulos locales, ~línea 90):

```python
import control_server
```

**b) En `main()`, después de crear los módulos (~línea 114), añadir:**

```python
    cancel_event = asyncio.Event()
```

**c) Añadir task del control server** después de `display_task` (~línea 133):

```python
    control_task = asyncio.create_task(
        control_server.run(cfg.control, cancel_event), name="control_server"
    )
```

**d) Actualizar `sm_task`** para pasar `cancel_event`:

```python
    sm_task = asyncio.create_task(
        sm_run(cfg, bus, audio, oww, gateway, playback, cancel_event), name="state_machine"
    )
```

**e) En el bloque `finally`, cancelar `control_task`** junto a las otras tasks:

```python
        sm_task.cancel()
        display_task.cancel()
        control_task.cancel()
        stop_task.cancel()

        await asyncio.gather(sm_task, display_task, control_task, stop_task, return_exceptions=True)
```

- [ ] **Step 2: Verificar que los tests de state_machine siguen pasando (regresión)**

```
python -m pytest client/test_state_machine.py client/test_gateway_client.py client/test_config_control.py client/test_control_server.py -v
```

Resultado esperado: todos los tests pasan (≥12 passed).

- [ ] **Step 3: Commit**

```bash
git add client/voice_client.py
git commit -m "feat(voice_client): arrancar control_server y pasar cancel_event a state_machine"
```

---

## Resumen de ficheros

| Fichero | Cambio |
|---|---|
| `client/gateway_client.py` | + `send_cancel()` |
| `client/control_server.py` | Nuevo — servidor HTTP asyncio |
| `client/state_machine.py` | + `_TurnCancelled`, `_safe_send_cancel`, cancel en `_recording`/`_responding`/`run` |
| `client/config.py` | + `ControlConfig`, `_control_from_dict`, `cfg.control` en `Config` |
| `client/voice_client.py` | + `cancel_event`, `control_task` |
| `config.example.yaml` | + bloque `control:` |
| `client/test_gateway_client.py` | Nuevo |
| `client/test_config_control.py` | Nuevo |
| `client/test_control_server.py` | Nuevo |
| `client/test_state_machine.py` | + 2 tests de cancel |
