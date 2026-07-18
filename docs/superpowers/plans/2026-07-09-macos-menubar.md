# macOS menubar UI for jota-voice — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a native macOS menubar item to `jota-voice` that lives in the same process as the voice client, exposes state from the existing `EventBus`, and offers pause/resume, open logs/config, shutdown service, and quit.

**Architecture:** A new `client/ui/` layer parallel to `backends/`, with a `MenubarBackend` Protocol and two implementations — `CocoaMenubarBackend` (pyobjc/AppKit, macOS only) and `NullMenubarBackend` (everywhere else). The Cocoa backend runs `NSApp.run()` on the main thread; asyncio runs on a worker thread. State flows from `EventBus` → `MenubarClient` → `_SharedState` (lock-protected) → 5 Hz `NSTimer` repaint. UI commands flow back via a thread-safe `queue.Queue` drained by an async loop in `MenubarClient`. The OWW task is wrapped in an `_oww_loop` that watches a `pause_event` so the pause toggle can cancel/recreate it.

**Tech Stack:** Python 3.11+, asyncio (stdlib), pyobjc-framework-Cocoa 10.0+ (macOS only), pytest (existing), existing Pydantic-free `@dataclass` config style, existing `EventBus` from `client/domain/event_bus.py`.

## Global Constraints

These apply to every task. Copy verbatim from the spec — do not weaken, raise, or relax them in any task.

- **Platform detection:** the registry MUST select `CocoaMenubarBackend` only on `sys.platform == "darwin"` AND only when `pyobjc-framework-Cocoa` imports successfully. Any other case returns `NullMenubarBackend`.
- **No new top-level dependencies on non-macOS.** `pyobjc-framework-Cocoa` is installed only from `requirements-macos.txt`, applied only by `install/macos/03-venv.sh`. It MUST NOT appear in any other `requirements*.txt` or in `pyproject.toml` runtime deps.
- **One process, two threads:** the menubar UI MUST live in the same process as the voice client. No IPC to a separate app, no HTTP polling from outside, no Unix socket to a helper.
- **Hexagonal layout:** `client/ui/` mirrors `client/backends/`. Tests mirror at `client/tests/ui/`. No business logic in `menubar_cocoa.py` — only AppKit plumbing.
- **Config style:** `client/config.py` uses plain `@dataclass`, NOT Pydantic. The new `MenubarConfig` follows that style.
- **Env override:** `JOTA_DISABLE_MENUBAR=1` MUST force `MenubarConfig.enabled = False`, regardless of YAML. Read it in `load_config`.
- **Out of scope (do not implement, do not stage):** bug hunting on the audio pipeline, `.app` packaging (py2app/pyinstaller), diagnostic window, kiosk launching from menu, persisting paused state across restarts, i18n, global keyboard shortcuts.
- **Spanish labels only** for now: "IDLE", "LISTENING", "THINKING", "SPEAKING", "Servicio", "Pausar escucha", "Reanudar escucha", "Apagar servicio", "Abrir logs", "Abrir configuración", "Acerca de jota-voice", "Salir".
- **SF Symbols only** for icons: `mic`, `ear`, `brain`, `speaker.wave.2`, `exclamationmark.triangle`. No PNG assets.
- **Refresh rate:** default 5 Hz (`refresh_hz=5.0`). Clamp `[1.0, 30.0]` in the dataclass `__post_init__`.
- **Shutdown sequence for "Apagar servicio":** `launchctl disable` FIRST, then `launchctl bootout`. The `disable` prevents `KeepAlive=true` from immediately respawning the agent. Both calls use `check=False`. After both run, set `stop_event` so the local client always exits.

---

## File Structure

**Created:**

| File | Responsibility |
|---|---|
| `client/ui/__init__.py` | Empty package marker. |
| `client/ui/menubar_base.py` | `MenubarBackend` Protocol, `MenubarCommands` dataclass, `_SharedState` dataclass. |
| `client/ui/menubar_null.py` | `NullMenubarBackend` — no-op implementation, logs at DEBUG. |
| `client/ui/menubar_client.py` | `MenubarClient` — subscribes to `EventBus`, drains `queue.Queue` for UI→asyncio commands, exposes `run()`. |
| `client/ui/menubar_cocoa.py` | `CocoaMenubarBackend` — pyobjc/AppKit: NSStatusItem, NSMenu, SF Symbols, NSTimer @ `refresh_hz`. |
| `client/tests/ui/__init__.py` | Empty package marker. |
| `client/tests/ui/test_menubar_base.py` | Tests `_SharedState` lock, Protocol conformance of `NullMenubarBackend`. |
| `client/tests/ui/test_menubar_null.py` | Tests no-op behaviour of `NullMenubarBackend`. |
| `client/tests/ui/test_menubar_client.py` | Tests event-to-backend mapping and queue-to-pause-event mapping without Cocoa. |
| `client/tests/ui/test_menubar_cocoa.py` | macOS-only tests gated by `sys.platform == "darwin"` and pyobjc import. |
| `client/requirements.txt` | New file. Currently `03-venv.sh` references it but it does not exist. Pins `sounddevice`, `numpy`, `pyyaml`. |
| `requirements-macos.txt` | New file. Pins `pyobjc-framework-Cocoa>=10.0`. |

**Modified:**

| File | What changes |
|---|---|
| `client/config.py` | Add `MenubarConfig` dataclass. Add `JOTA_DISABLE_MENUBAR` env override in `load_config`. Wire `MenubarConfig` into `Config`. |
| `client/backends/registry.py` | Add `make_menubar(cfg)` factory following the pattern of `make_audio/make_display/make_oww`. |
| `client/app/voice_client.py` | Build menubar backend, build `MenubarClient`, build `MenubarCommands` closures, refactor OWW task to `_oww_loop(pause_event)`, add teardown for Cocoa thread in `finally`. |
| `client/tests/backends/test_registry.py` | Add tests for `make_menubar` (null/darwin/import-fail). |
| `config.example.yaml` | Add commented `menubar:` section so users discover it. |
| `install/macos/03-venv.sh` | Create `client/requirements.txt` (no, see Task 8); install `requirements-macos.txt` if present. |
| `README.md` | Add "macOS menubar" subsection under macOS install. |

---

## Task 1: `MenubarConfig` dataclass in `config.py`

**Files:**
- Modify: `client/config.py:85-110` (add `MenubarConfig` after `DisplayConfig`; add field to `Config`)
- Modify: `client/config.py:186-203` (extend `load_config` to read `menubar:` section and apply `JOTA_DISABLE_MENUBAR`)
- Test: `client/tests/test_config.py` (extend — add `test_menubar_default_disabled_false`, `test_menubar_env_override`, `test_menubar_yaml_override`, `test_menubar_refresh_clamped`)

**Interfaces:**
- Consumes: existing `load_config` flow.
- Produces: `MenubarConfig` dataclass with fields `enabled: bool = True`, `refresh_hz: float = 5.0`, `log_path: Optional[str] = None`, `config_path: Optional[str] = None`. `__post_init__` MUST clamp `refresh_hz` into `[1.0, 30.0]`. A `Config` instance has a `.menubar` attribute.

- [ ] **Step 1: Write the failing tests**

Append to `client/tests/test_config.py` (read the file first; the test layout already imports `Config` and constructs it directly):

```python
import os
import pytest
from config import Config, GatewayConfig, MenubarConfig, load_config


def _minimal_yaml(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "gateway:\n  host: 127.0.0.1\n  client_key: x\ndevice:\n  id: t\n"
    )
    return p


def test_menubar_default_enabled():
    cfg = load_config(_minimal_yaml(__import__("pathlib").Path("/tmp")))
    assert isinstance(cfg.menubar, MenubarConfig)
    assert cfg.menubar.enabled is True
    assert cfg.menubar.refresh_hz == 5.0
    assert cfg.menubar.log_path is None
    assert cfg.menubar.config_path is None


def test_menubar_env_disable(monkeypatch, tmp_path):
    monkeypatch.setenv("JOTA_DISABLE_MENUBAR", "1")
    cfg = load_config(_minimal_yaml(tmp_path))
    assert cfg.menubar.enabled is False


def test_menubar_yaml_override(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "gateway:\n  host: 127.0.0.1\n  client_key: x\n"
        "device:\n  id: t\n"
        "menubar:\n  enabled: false\n  refresh_hz: 10.0\n"
    )
    cfg = load_config(p)
    assert cfg.menubar.enabled is False
    assert cfg.menubar.refresh_hz == 10.0


def test_menubar_refresh_clamped_low():
    cfg = MenubarConfig(refresh_hz=0.1)
    assert cfg.refresh_hz == 1.0


def test_menubar_refresh_clamped_high():
    cfg = MenubarConfig(refresh_hz=999.0)
    assert cfg.refresh_hz == 30.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/alfonsogarre/Workspace/jota-voice/client && PYTHONPATH=. pytest ../client/tests/test_config.py -v`
Expected: `ImportError` for `MenubarConfig` (or 5 collection errors).

- [ ] **Step 3: Add `MenubarConfig` dataclass**

In `client/config.py`, after the `DisplayConfig` block (around line 90), add:

```python
@dataclass
class MenubarConfig:
    enabled: bool = True
    refresh_hz: float = 5.0
    log_path: Optional[str] = None
    config_path: Optional[str] = None

    def __post_init__(self) -> None:
        if self.refresh_hz < 1.0:
            self.refresh_hz = 1.0
        elif self.refresh_hz > 30.0:
            self.refresh_hz = 30.0
```

In the `Config` dataclass (around line 103), add a field:

```python
menubar: MenubarConfig = field(default_factory=MenubarConfig)
```

In `load_config` (around line 186-203), add at the top:

```python
def _menubar_from_dict(d: dict) -> MenubarConfig:
    return MenubarConfig(
        enabled=bool(d.get("enabled", True)),
        refresh_hz=float(d.get("refresh_hz", 5.0)),
        log_path=d.get("log_path"),
        config_path=d.get("config_path"),
    )
```

In the `Config(...)` construction call inside `load_config`, add:

```python
menubar=_menubar_from_dict(data.get("menubar", {})),
```

Then, just before constructing `Config`, apply the env override:

```python
menubar_section = data.get("menubar", {})
if os.environ.get("JOTA_DISABLE_MENUBAR") in ("1", "true", "yes"):
    menubar_section = {**menubar_section, "enabled": False}
```

and pass `menubar=_menubar_from_dict(menubar_section)` instead.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/alfonsogarre/Workspace/jota-voice/client && PYTHONPATH=. pytest ../client/tests/test_config.py -v`
Expected: all 4 new tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/alfonsogarre/Workspace/jota-voice
git add client/config.py client/tests/test_config.py
git commit -m "feat(config): añadir MenubarConfig con env override JOTA_DISABLE_MENUBAR

Dataclass con enabled/refresh_hz/log_path/config_path. refresh_hz se
clampa a [1.0, 30.0]. La variable de entorno JOTA_DISABLE_MENUBAR=1
fuerza enabled=False independientemente del YAML.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `client/ui/__init__.py` + empty test package

**Files:**
- Create: `client/ui/__init__.py`
- Create: `client/tests/ui/__init__.py`

- [ ] **Step 1: Create the two empty package files**

`client/ui/__init__.py`:
```python
"""UI backends intercambiables por plataforma (actualmente: macOS menubar)."""
```

`client/tests/ui/__init__.py`:
```python
```

- [ ] **Step 2: Verify packages import**

Run: `cd /Users/alfonsogarre/Workspace/jota-voice/client && PYTHONPATH=. python -c "import ui; import tests.ui"`
Expected: no error.

- [ ] **Step 3: Commit**

```bash
cd /Users/alfonsogarre/Workspace/jota-voice
git add client/ui/__init__.py client/tests/ui/__init__.py
git commit -m "chore(ui): crear paquetes vacíos client/ui y client/tests/ui

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `menubar_base.py` — Protocol, commands, shared state

**Files:**
- Create: `client/ui/menubar_base.py`
- Test: `client/tests/ui/test_menubar_base.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `class MenubarBackend(Protocol)` with `set_state`, `set_status_text`, `set_listening_paused`, `set_errors_count`, `set_commands` — all `None`-returning.
  - `@dataclass class MenubarCommands` with five zero-arg callables: `on_toggle_pause`, `on_open_logs`, `on_open_config`, `on_shutdown_service`, `on_quit`.
  - `@dataclass class _SharedState` with `state: str = "idle"`, `last_text: str = ""`, `errors_count: int = 0`, `listening_paused: bool = False`, and a `_lock: threading.Lock`. Provide `read() -> tuple[str, str, int, bool]` and `update(...)` methods that take/release the lock.

- [ ] **Step 1: Write the failing tests**

`client/tests/ui/test_menubar_base.py`:
```python
"""Tests for the MenubarBackend Protocol and _SharedState thread-safety."""
from __future__ import annotations

import threading

from ui.menubar_base import MenubarBackend, MenubarCommands, _SharedState


def test_shared_state_defaults():
    s = _SharedState()
    assert s.read() == ("idle", "", 0, False)


def test_shared_state_update_and_read():
    s = _SharedState()
    s.update(state="listening", last_text="hola", errors_count=2, listening_paused=True)
    assert s.read() == ("listening", "hola", 2, True)


def test_shared_state_partial_update():
    s = _SharedState()
    s.update(state="speaking")
    # other fields preserved
    assert s.read()[0] == "speaking"
    assert s.read()[1] == ""
    assert s.read()[2] == 0
    assert s.read()[3] is False


def test_shared_state_lock_no_deadlock():
    s = _SharedState()
    errors: list[Exception] = []

    def writer():
        try:
            for _ in range(1000):
                s.update(state="listening", errors_count=1)
                s.update(state="idle")
        except Exception as e:
            errors.append(e)

    def reader():
        try:
            for _ in range(1000):
                _ = s.read()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
        assert not t.is_alive(), "thread deadlocked"
    assert not errors, f"thread errors: {errors}"


def test_menubar_commands_callables():
    called: list[str] = []

    def make(name: str):
        return lambda: called.append(name)

    cmds = MenubarCommands(
        on_toggle_pause=make("pause"),
        on_open_logs=make("logs"),
        on_open_config=make("config"),
        on_shutdown_service=make("shutdown"),
        on_quit=make("quit"),
    )
    cmds.on_toggle_pause()
    cmds.on_open_logs()
    cmds.on_open_config()
    cmds.on_shutdown_service()
    cmds.on_quit()
    assert called == ["pause", "logs", "config", "shutdown", "quit"]


def test_protocol_is_runtime_checkable():
    class Fake:
        def set_state(self, state: str) -> None: ...
        def set_status_text(self, text: str) -> None: ...
        def set_listening_paused(self, paused: bool) -> None: ...
        def set_errors_count(self, n: int) -> None: ...
        def set_commands(self, cmds) -> None: ...

    assert isinstance(Fake(), MenubarBackend)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/alfonsogarre/Workspace/jota-voice/client && PYTHONPATH=. pytest ../client/tests/ui/test_menubar_base.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'ui.menubar_base'`.

- [ ] **Step 3: Implement `menubar_base.py`**

`client/ui/menubar_base.py`:
```python
"""Contratos del UI layer: Protocol, comandos y estado compartido."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable


@runtime_checkable
class MenubarBackend(Protocol):
    """Contrato que cualquier backend de UI debe cumplir."""

    def set_state(self, state: str) -> None: ...
    def set_status_text(self, text: str) -> None: ...
    def set_listening_paused(self, paused: bool) -> None: ...
    def set_errors_count(self, n: int) -> None: ...
    def set_commands(self, cmds: "MenubarCommands") -> None: ...


@dataclass
class MenubarCommands:
    """Callbacks que la UI invoca hacia asyncio. La UI no escribe al bus."""

    on_toggle_pause: Callable[[], None]
    on_open_logs: Callable[[], None]
    on_open_config: Callable[[], None]
    on_shutdown_service: Callable[[], None]
    on_quit: Callable[[], None]


@dataclass
class _SharedState:
    """Estado proyectado del EventBus para el hilo de Cocoa.

    El hilo asyncio (MenubarClient) lo escribe; el hilo Cocoa (NSTimer) lo lee.
    El lock evita lecturas a medias en campos de más de 4 bytes (Python ints
    en CPython son atómicos, pero el snapshot consistente de los 4 campos sí
    necesita lock).
    """

    state: str = "idle"
    last_text: str = ""
    errors_count: int = 0
    listening_paused: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def read(self) -> tuple[str, str, int, bool]:
        with self._lock:
            return (self.state, self.last_text, self.errors_count, self.listening_paused)

    def update(
        self,
        state: str | None = None,
        last_text: str | None = None,
        errors_count: int | None = None,
        listening_paused: bool | None = None,
    ) -> None:
        with self._lock:
            if state is not None:
                self.state = state
            if last_text is not None:
                self.last_text = last_text
            if errors_count is not None:
                self.errors_count = errors_count
            if listening_paused is not None:
                self.listening_paused = listening_paused
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/alfonsogarre/Workspace/jota-voice/client && PYTHONPATH=. pytest ../client/tests/ui/test_menubar_base.py -v`
Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/alfonsogarre/Workspace/jota-voice
git add client/ui/menubar_base.py client/tests/ui/test_menubar_base.py
git commit -m "feat(ui): Protocol MenubarBackend + _SharedState thread-safe

MenubarCommands expone 5 callbacks zero-arg que la UI invoca hacia
asyncio. _SharedState usa threading.Lock y un update() parcial para
que asyncio pueda mutar campos sin pisar el snapshot que lee Cocoa.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `menubar_null.py` — no-op backend

**Files:**
- Create: `client/ui/menubar_null.py`
- Test: `client/tests/ui/test_menubar_null.py`

**Interfaces:**
- Consumes: `MenubarBackend` Protocol (must satisfy it).
- Produces: `class NullMenubarBackend` whose 5 methods log at DEBUG and return `None`.

- [ ] **Step 1: Write the failing tests**

`client/tests/ui/test_menubar_null.py`:
```python
"""Tests for the NullMenubarBackend (used on non-macOS and in tests)."""
from __future__ import annotations

import logging

from ui.menubar_base import MenubarBackend, MenubarCommands
from ui.menubar_null import NullMenubarBackend


def test_null_backend_satisfies_protocol():
    assert isinstance(NullMenubarBackend(), MenubarBackend)


def test_null_methods_return_none(caplog):
    b = NullMenubarBackend()
    with caplog.at_level(logging.DEBUG, logger="ui.menubar_null"):
        assert b.set_state("listening") is None
        assert b.set_status_text("hola") is None
        assert b.set_listening_paused(True) is None
        assert b.set_errors_count(3) is None
        cmds = MenubarCommands(
            on_toggle_pause=lambda: None,
            on_open_logs=lambda: None,
            on_open_config=lambda: None,
            on_shutdown_service=lambda: None,
            on_quit=lambda: None,
        )
        assert b.set_commands(cmds) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/alfonsogarre/Workspace/jota-voice/client && PYTHONPATH=. pytest ../client/tests/ui/test_menubar_null.py -v`
Expected: `ModuleNotFoundError: No module named 'ui.menubar_null'`.

- [ ] **Step 3: Implement `menubar_null.py`**

`client/ui/menubar_null.py`:
```python
"""NullMenubarBackend — backend no-op para Linux, Termux, Windows y tests."""
from __future__ import annotations

import logging

from .menubar_base import MenubarCommands

log = logging.getLogger(__name__)


class NullMenubarBackend:
    """Implementación no-op. Loguea a DEBUG; nunca falla."""

    def set_state(self, state: str) -> None:
        log.debug("NullMenubarBackend: state=%s", state)

    def set_status_text(self, text: str) -> None:
        log.debug("NullMenubarBackend: text=%r", text)

    def set_listening_paused(self, paused: bool) -> None:
        log.debug("NullMenubarBackend: paused=%s", paused)

    def set_errors_count(self, n: int) -> None:
        log.debug("NullMenubarBackend: errors=%d", n)

    def set_commands(self, cmds: MenubarCommands) -> None:
        log.debug("NullMenubarBackend: commands registered")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/alfonsogarre/Workspace/jota-voice/client && PYTHONPATH=. pytest ../client/tests/ui/test_menubar_null.py -v`
Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/alfonsogarre/Workspace/jota-voice
git add client/ui/menubar_null.py client/tests/ui/test_menubar_null.py
git commit -m "feat(ui): NullMenubarBackend no-op

Satisface MenubarBackend para uso en Linux, Termux, Windows y tests.
Nunca falla — loguea a DEBUG y retorna None.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `menubar_client.py` — EventBus subscriber + queue drainer

**Files:**
- Create: `client/ui/menubar_client.py`
- Test: `client/tests/ui/test_menubar_client.py`

**Interfaces:**
- Consumes: `MenubarBackend`, `EventBus`, `queue.Queue` (thread-safe), `asyncio.Event` (for pause).
- Produces: `class MenubarClient(backend)` with `async run(bus, ui_queue, pause_event)`. Maps:
  - `state_changed` → `backend.set_state(event.data["state"])`.
  - `transcription` → `backend.set_status_text(event.data["text"])`.
  - `error` → `backend.set_errors_count(local_count)`.
  - UI queue items: `"toggle_pause"` flips `pause_event`, `"open_logs"`, `"open_config"`, `"shutdown_service"`, `"quit"`.

- [ ] **Step 1: Write the failing tests**

`client/tests/ui/test_menubar_client.py`:
```python
"""Tests offline de MenubarClient: mapeo de eventos y drenaje de cola."""
from __future__ import annotations

import asyncio
import queue
import threading

import pytest

from domain.event_bus import EventBus, VoiceEvent
from ui.menubar_base import MenubarCommands
from ui.menubar_client import MenubarClient


class FakeBackend:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def set_state(self, state: str) -> None:
        self.calls.append(("set_state", (state,)))

    def set_status_text(self, text: str) -> None:
        self.calls.append(("set_status_text", (text,)))

    def set_listening_paused(self, paused: bool) -> None:
        self.calls.append(("set_listening_paused", (paused,)))

    def set_errors_count(self, n: int) -> None:
        self.calls.append(("set_errors_count", (n,)))

    def set_commands(self, cmds: MenubarCommands) -> None:
        self.calls.append(("set_commands", (id(cmds),)))


def _run(coro):
    return asyncio.run(coro)


def test_state_changed_propagates():
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        task = asyncio.create_task(client.run(bus, ui_queue, pause))
        await asyncio.sleep(0.05)  # let subscription register
        bus.publish(VoiceEvent(type="state_changed", data={"state": "listening"}))
        bus.publish(VoiceEvent(type="transcription", data={"text": "hola"}))
        await asyncio.sleep(0.05)
        bus.close()
        await asyncio.wait_for(task, timeout=2.0)

    _run(scenario())

    methods = [c[0] for c in backend.calls]
    assert "set_state" in methods
    assert "set_status_text" in methods
    set_state_vals = [c[1][0] for c in backend.calls if c[0] == "set_state"]
    assert "listening" in set_state_vals
    set_text_vals = [c[1][0] for c in backend.calls if c[0] == "set_status_text"]
    assert "hola" in set_text_vals


def test_error_increments_count():
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        task = asyncio.create_task(client.run(bus, ui_queue, pause))
        await asyncio.sleep(0.05)
        bus.publish(VoiceEvent(type="error", data={"message": "boom"}))
        bus.publish(VoiceEvent(type="error", data={"message": "boom2"}))
        await asyncio.sleep(0.05)
        bus.close()
        await asyncio.wait_for(task, timeout=2.0)

    _run(scenario())
    err_vals = sorted(c[1][0] for c in backend.calls if c[0] == "set_errors_count")
    assert err_vals == [1, 2]


def test_toggle_pause_flips_event():
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()
    # simulate UI thread pushing commands
    ui_queue.put_nowait("toggle_pause")
    ui_queue.put_nowait("toggle_pause")

    async def scenario():
        task = asyncio.create_task(client.run(bus, ui_queue, pause))
        # give it time to drain two toggle_pause and a third to confirm state
        ui_queue.put_nowait("toggle_pause")
        await asyncio.sleep(0.1)
        bus.close()
        await asyncio.wait_for(task, timeout=2.0)

    _run(scenario())

    # After three toggles: set, clear, set -> paused=True
    assert pause.is_set()
    paused_calls = [c[1][0] for c in backend.calls if c[0] == "set_listening_paused"]
    assert paused_calls == [True, False, True]


def test_run_returns_on_bus_close():
    bus = EventBus()
    backend = FakeBackend()
    client = MenubarClient(backend)
    ui_queue: queue.Queue = queue.Queue()
    pause = asyncio.Event()

    async def scenario():
        task = asyncio.create_task(client.run(bus, ui_queue, pause))
        await asyncio.sleep(0.05)
        bus.close()
        await asyncio.wait_for(task, timeout=2.0)
        return task.result()

    _run(scenario())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/alfonsogarre/Workspace/jota-voice/client && PYTHONPATH=. pytest ../client/tests/ui/test_menubar_client.py -v`
Expected: `ModuleNotFoundError: No module named 'ui.menubar_client'`.

- [ ] **Step 3: Implement `menubar_client.py`**

`client/ui/menubar_client.py`:
```python
"""MenubarClient — suscriptor del EventBus que traduce VoiceEvent a
llamadas sobre un MenubarBackend inyectado, y drena una queue.Queue
thread-safe para los comandos que la UI envía hacia asyncio.
"""
from __future__ import annotations

import asyncio
import logging
import queue

from domain.event_bus import EventBus, VoiceEvent

from .menubar_base import MenubarBackend

log = logging.getLogger(__name__)


class MenubarClient:
    def __init__(self, backend: MenubarBackend) -> None:
        self._backend = backend
        self._errors_count = 0

    async def run(
        self,
        bus: EventBus,
        ui_queue: queue.Queue,
        pause_event: asyncio.Event,
    ) -> None:
        """Loop suscrito al bus. Se cancela externamente (asyncio.CancelledError).

        El bus publica eventos asyncio-safe; la ui_queue es thread-safe
        (queue.Queue) y la drenamos desde un executor para no bloquear el
        loop mientras la UI está inactiva.
        """
        loop = asyncio.get_running_loop()

        async def _drain_queue() -> None:
            while True:
                cmd = await loop.run_in_executor(None, ui_queue.get)
                if cmd == "toggle_pause":
                    if pause_event.is_set():
                        pause_event.clear()
                    else:
                        pause_event.set()
                    self._backend.set_listening_paused(pause_event.is_set())
                    log.info("MenubarClient: toggle_pause -> paused=%s", pause_event.is_set())
                elif cmd == "open_logs":
                    log.info("MenubarClient: open_logs requested")
                    # Acción manejada por el caller (voice_client.main); aquí
                    # sólo logueamos para mantener el cliente agnóstico.
                elif cmd == "open_config":
                    log.info("MenubarClient: open_config requested")
                elif cmd == "shutdown_service":
                    log.info("MenubarClient: shutdown_service requested")
                elif cmd == "quit":
                    log.info("MenubarClient: quit requested")
                else:
                    log.warning("MenubarClient: comando UI desconocido: %r", cmd)

        drain_task = asyncio.create_task(_drain_queue())

        try:
            async for event in bus.subscribe():
                await self._handle(event)
        finally:
            drain_task.cancel()
            try:
                await drain_task
            except asyncio.CancelledError:
                pass

    async def _handle(self, event: VoiceEvent) -> None:
        if not isinstance(event.data, dict):
            return

        if event.type == "state_changed":
            self._backend.set_state(event.data.get("state", ""))
        elif event.type == "transcription":
            self._backend.set_status_text(event.data.get("text", ""))
        elif event.type == "error":
            self._errors_count += 1
            self._backend.set_errors_count(self._errors_count)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/alfonsogarre/Workspace/jota-voice/client && PYTHONPATH=. pytest ../client/tests/ui/test_menubar_client.py -v`
Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/alfonsogarre/Workspace/jota-voice
git add client/ui/menubar_client.py client/tests/ui/test_menubar_client.py
git commit -m "feat(ui): MenubarClient suscribe al bus y drena cola UI

Mapea state_changed/transcription/error a llamadas del backend. Drena
una queue.Queue thread-safe en un executor para no bloquear el loop
de asyncio cuando la UI está inactiva.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: `make_menubar` factory in `registry.py`

**Files:**
- Modify: `client/backends/registry.py` (add `make_menubar` after `make_oww`)
- Modify: `client/tests/backends/test_registry.py` (add 4 tests)

**Interfaces:**
- Consumes: `Config` with `menubar: MenubarConfig`.
- Produces: `make_menubar(cfg) -> MenubarBackend`. Returns `NullMenubarBackend()` if `cfg.menubar.enabled is False`. On darwin with pyobjc available: returns `CocoaMenubarBackend(cfg.menubar)`. Otherwise: `NullMenubarBackend()` with a WARNING log when pyobjc was the only reason it could have worked.

- [ ] **Step 1: Write the failing tests**

Append to `client/tests/backends/test_registry.py`:
```python
from config import MenubarConfig


def _cfg_with_menubar(enabled: bool = True) -> Config:
    return Config(
        gateway=GatewayConfig(host="127.0.0.1", client_key="x"),
        device=DeviceConfig(id="test"),
        menubar=MenubarConfig(enabled=enabled),
    )


def test_make_menubar_disabled_returns_null():
    inst = registry.make_menubar(_cfg_with_menubar(enabled=False))
    assert inst.__class__.__name__ == "NullMenubarBackend"


def test_make_menubar_linux_returns_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry.sys, "platform", "linux")
    inst = registry.make_menubar(_cfg_with_menubar())
    assert inst.__class__.__name__ == "NullMenubarBackend"


def test_make_menubar_darwin_no_pyobjc_returns_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry.sys, "platform", "darwin")

    def _raise():
        raise ImportError("no pyobjc")

    import sys as _sys

    monkeypatch.setitem(_sys.modules, "ui.menubar_cocoa", None)
    # Force ImportError on the lazy import inside make_menubar
    orig_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def fake_import(name, *args, **kwargs):
        if name == "ui.menubar_cocoa" or name.endswith(".menubar_cocoa"):
            raise ImportError("no pyobjc")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    inst = registry.make_menubar(_cfg_with_menubar())
    assert inst.__class__.__name__ == "NullMenubarBackend"


def test_make_menubar_darwin_with_pyobjc_returns_cocoa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry.sys, "platform", "darwin")
    # Insert a fake Cocoa module
    import types
    import sys as _sys

    fake_cocoa = types.ModuleType("ui.menubar_cocoa")

    class FakeCocoaBackend:
        def __init__(self, cfg):
            self.cfg = cfg

    fake_cocoa.CocoaMenubarBackend = FakeCocoaBackend
    monkeypatch.setitem(_sys.modules, "ui.menubar_cocoa", fake_cocoa)
    inst = registry.make_menubar(_cfg_with_menubar())
    assert isinstance(inst, FakeCocoaBackend)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/alfonsogarre/Workspace/jota-voice/client && PYTHONPATH=. pytest ../client/tests/backends/test_registry.py -v`
Expected: collection error / `AttributeError: module 'backends.registry' has no attribute 'make_menubar'`.

- [ ] **Step 3: Add `make_menubar` to `registry.py`**

In `client/backends/registry.py`, append after the `make_oww` function:

```python
def make_menubar(cfg: Config):
    from ui.menubar_null import NullMenubarBackend

    if not cfg.menubar.enabled:
        return NullMenubarBackend()

    if sys.platform != "darwin":
        return NullMenubarBackend()

    try:
        from ui.menubar_cocoa import CocoaMenubarBackend
    except ImportError:
        logging.getLogger(__name__).warning(
            "pyobjc-framework-Cocoa no disponible; menubar UI desactivada. "
            "Instala con: pip install pyobjc-framework-Cocoa"
        )
        return NullMenubarBackend()

    return CocoaMenubarBackend(cfg.menubar)
```

Add at the top of `registry.py` (after the existing `import sys`):

```python
import logging
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/alfonsogarre/Workspace/jota-voice/client && PYTHONPATH=. pytest ../client/tests/backends/test_registry.py -v`
Expected: all existing tests still pass + 4 new tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/alfonsogarre/Workspace/jota-voice
git add client/backends/registry.py client/tests/backends/test_registry.py
git commit -m "feat(registry): make_menubar factory

Selecciona NullMenubarBackend si enabled=False, si la plataforma no
es darwin, o si pyobjc-framework-Cocoa no está instalado (con WARNING).
Si todo encaja, devuelve CocoaMenubarBackend.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: `menubar_cocoa.py` — pyobjc/AppKit implementation

**Files:**
- Create: `client/ui/menubar_cocoa.py`
- Test: `client/tests/ui/test_menubar_cocoa.py`

**Interfaces:**
- Consumes: `MenubarConfig`, `_SharedState`.
- Produces: `class CocoaMenubarBackend(cfg)`. `__init__` constructs the NSStatusItem, builds the NSMenu with the structure from the spec, registers the NSTimer at `1.0 / cfg.refresh_hz` seconds. `set_state/set_status_text/set_listening_paused/set_errors_count/set_commands` all mutate the backend's view; the timer repaints. `start()` launches `NSApp.run()` on a daemon thread.

- [ ] **Step 1: Write the failing tests**

`client/tests/ui/test_menubar_cocoa.py`:
```python
"""Tests Cocoa-only: skipped salvo en macOS con pyobjc disponible."""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="Cocoa tests only run on macOS"
)

pyobjc_available = False
try:
    import AppKit  # noqa: F401
    pyobjc_available = True
except ImportError:
    pass

if not pyobjc_available:
    pytestmark = pytest.mark.skip(reason="pyobjc not installed")


@pytest.fixture
def backend():
    from config import MenubarConfig
    from ui.menubar_base import _SharedState
    from ui.menubar_cocoa import CocoaMenubarBackend

    cfg = MenubarConfig(enabled=True, refresh_hz=20.0)  # 50ms tick for fast tests
    shared = _SharedState()
    b = CocoaMenubarBackend(cfg, shared)
    yield b
    b.stop()


def test_status_item_created(backend):
    assert backend.status_item is not None
    assert backend.status_item.button() is not None


def test_set_state_updates_shared(backend):
    backend.set_state("listening")
    assert backend._shared.read()[0] == "listening"


def test_set_listening_paused_updates_label_after_tick(backend):
    """Tras un tick del timer, la etiqueta del item de pausa refleja el estado."""
    backend.set_listening_paused(True)
    import time
    time.sleep(0.2)  # 4 ticks a 20Hz
    pause_item = backend._pause_menu_item
    assert pause_item.title() == "Reanudar escucha"


def test_set_commands_does_not_raise(backend):
    from ui.menubar_base import MenubarCommands

    cmds = MenubarCommands(
        on_toggle_pause=lambda: None,
        on_open_logs=lambda: None,
        on_open_config=lambda: None,
        on_shutdown_service=lambda: None,
        on_quit=lambda: None,
    )
    backend.set_commands(cmds)
```

- [ ] **Step 2: Run tests to verify they fail (or skip on non-darwin)**

Run: `cd /Users/alfonsogarre/Workspace/jota-voice/client && PYTHONPATH=. pytest ../client/tests/ui/test_menubar_cocoa.py -v`
Expected: skip on non-darwin, otherwise `ModuleNotFoundError: No module named 'ui.menubar_cocoa'`.

- [ ] **Step 3: Implement `menubar_cocoa.py`**

`client/ui/menubar_cocoa.py`:
```python
"""CocoaMenubarBackend — NSStatusItem nativo de macOS con pyobjc.

IMPORTANTE: este módulo importa AppKit al cargarse. El registry solo lo
intenta en darwin Y captura ImportError si pyobjc no está instalado.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

import AppKit
import Foundation

from config import MenubarConfig

from .menubar_base import MenubarCommands, _SharedState

log = logging.getLogger(__name__)


# Mapeo de estado a SF Symbol. SF Symbols es el set de iconos vectoriales
# incluido en macOS; "template=True" deja que el sistema pinte según tema.
_STATE_ICONS = {
    "idle":      "mic",
    "listening": "ear",
    "thinking":  "brain",
    "speaking":  "speaker.wave.2",
    "error":     "exclamationmark.triangle",
}


class CocoaMenubarBackend:
    def __init__(self, cfg: MenubarConfig, shared: Optional[_SharedState] = None) -> None:
        self._cfg = cfg
        self._shared = shared or _SharedState()
        self._commands: Optional[MenubarCommands] = None
        self._app: Optional[AppKit.NSApplication] = None
        self._status_item: Optional[AppKit.NSStatusItem] = None
        self._menu: Optional[AppKit.NSMenu] = None
        self._header_item: Optional[AppKit.NSMenuItem] = None
        self._pause_item: Optional[AppKit.NSMenuItem] = None
        self._timer: Optional[Foundation.NSTimer] = None
        self._thread: Optional[threading.Thread] = None

        self._build_status_item()

    # ------------------------------------------------------------------
    # Construcción
    # ------------------------------------------------------------------

    def _build_status_item(self) -> None:
        self._status_item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
            AppKit.NSVariableStatusItemLength
        )
        if self._status_item.button() is not None:
            self._status_item.button().setImage_(self._icon_for_state(self._shared.state))
            self._status_item.button().setImagePosition_(AppKit.NSImageOnly)
            self._status_item.button().setToolTip_("jota-voice")

        self._menu = AppKit.NSMenu.alloc().init()
        self._status_item.setMenu_(self._menu)

        # 1. Cabecera no seleccionable
        self._header_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            self._header_title(), None, ""
        )
        self._header_item.setEnabled_(False)
        self._menu.addItem_(self._header_item)

        # 2. Submenú "Servicio"
        servicio_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Servicio", None, ""
        )
        servicio_menu = AppKit.NSMenu.alloc().init()
        self._pause_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Pausar escucha", "togglePause:", ""
        )
        self._pause_item.setTarget_(self)
        servicio_menu.addItem_(self._pause_item)
        shutdown_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Apagar servicio", "shutdownService:", ""
        )
        shutdown_item.setTarget_(self)
        servicio_menu.addItem_(shutdown_item)
        servicio_item.setSubmenu_(servicio_menu)
        self._menu.addItem_(servicio_item)

        self._menu.addItem_(AppKit.NSMenuItem.separatorItem())

        # 4. Abrir logs
        logs_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Abrir logs", "openLogs:", ""
        )
        logs_item.setTarget_(self)
        self._menu.addItem_(logs_item)

        # 5. Abrir configuración
        cfg_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Abrir configuración", "openConfig:", ""
        )
        cfg_item.setTarget_(self)
        self._menu.addItem_(cfg_item)

        self._menu.addItem_(AppKit.NSMenuItem.separatorItem())

        # 7. Acerca de
        about_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Acerca de jota-voice", "showAbout:", ""
        )
        about_item.setTarget_(self)
        self._menu.addItem_(about_item)

        # 8. Salir
        quit_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Salir", "quitApp:", ""
        )
        quit_item.setTarget_(self)
        self._menu.addItem_(quit_item)

    def _icon_for_state(self, state: str) -> AppKit.NSImage:
        symbol_name = _STATE_ICONS.get(state, "mic")
        image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            symbol_name, None
        )
        if image is not None:
            image.setTemplate_(True)
            return image
        # Fallback a un punto si el SF Symbol no existe en esta versión de macOS
        return AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "circle", None
        )

    def _header_title(self) -> str:
        state, _, errors, paused = self._shared.read()
        suffix = " (pausado)" if paused else ""
        if errors:
            return f"{state.upper()} — {errors} error(es){suffix}"
        return f"{state.upper()}{suffix}"

    # ------------------------------------------------------------------
    # API pública (llamada desde asyncio a través de _SharedState o
    # directamente desde el hilo Cocoa vía performSelectorOnMainThread)
    # ------------------------------------------------------------------

    def set_state(self, state: str) -> None:
        self._shared.update(state=state)

    def set_status_text(self, text: str) -> None:
        self._shared.update(last_text=text)

    def set_listening_paused(self, paused: bool) -> None:
        self._shared.update(listening_paused=paused)

    def set_errors_count(self, n: int) -> None:
        self._shared.update(errors_count=n)

    def set_commands(self, cmds: MenubarCommands) -> None:
        self._commands = cmds

    # ------------------------------------------------------------------
    # Arranque del runloop Cocoa
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Arranca NSApp.run() en un hilo daemon. El caller debe haber
        construido self._status_item y configurado los commands."""

        def _run() -> None:
            self._app = AppKit.NSApplication.sharedApplication()
            self._app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

            interval = 1.0 / max(1.0, min(30.0, self._cfg.refresh_hz))
            self._timer = Foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                interval, self, "tick:", None, True
            )
            Foundation.NSRunLoop.currentRunLoop().addTimer_forMode_(
                self._timer, Foundation.NSRunLoopCommonModes
            )
            self._app.run()

        self._thread = threading.Thread(target=_run, name="cocoa-menubar", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Detiene NSApp y espera al hilo. Llamar desde asyncio."""

        def _terminate() -> None:
            if self._app is not None:
                self._app.terminate_(None)

        if self._app is not None:
            self._app.performSelectorOnMainThread_withObject_waitUntilDone_(
                _terminate, None, False
            )
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    # ------------------------------------------------------------------
    # Tick del NSTimer (corre en hilo Cocoa)
    # ------------------------------------------------------------------

    def tick_(self, _timer) -> None:  # noqa: D401 — selector name
        state, last_text, _errors, paused = self._shared.read()

        if self._status_item and self._status_item.button():
            self._status_item.button().setImage_(self._icon_for_state(state))
            self._status_item.button().setTitle_(self._title_for_state(state))

        if self._header_item is not None:
            self._header_item.setTitle_(self._header_title())

        if self._pause_item is not None:
            self._pause_item.setTitle_(
                "Reanudar escucha" if paused else "Pausar escucha"
            )

    def _title_for_state(self, state: str) -> str:
        # El estado se ve también en el icono de la cabecera; dejamos el
        # botón solo con el icono salvo que el sistema no soporte SF Symbol.
        return ""

    # ------------------------------------------------------------------
    # Acciones de menú (corren en hilo Cocoa)
    # ------------------------------------------------------------------

    def togglePause_(self, _sender) -> None:
        if self._commands is not None:
            self._commands.on_toggle_pause()

    def shutdownService_(self, _sender) -> None:
        if self._commands is not None:
            self._commands.on_shutdown_service()

    def openLogs_(self, _sender) -> None:
        if self._commands is not None:
            self._commands.on_open_logs()

    def openConfig_(self, _sender) -> None:
        if self._commands is not None:
            self._commands.on_open_config()

    def showAbout_(self, _sender) -> None:
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("jota-voice")
        alert.setInformativeText_("Cliente de voz universal del ecosistema Jota.")
        alert.runModal()

    def quitApp_(self, _sender) -> None:
        if self._commands is not None:
            self._commands.on_quit()
```

- [ ] **Step 4: Run tests to verify they pass (or skip on non-darwin)**

Run: `cd /Users/alfonsogarre/Workspace/jota-voice/client && PYTHONPATH=. pytest ../client/tests/ui/test_menubar_cocoa.py -v`
Expected: skipped on Linux; on macOS with pyobjc, all 4 pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/alfonsogarre/Workspace/jota-voice
git add client/ui/menubar_cocoa.py client/tests/ui/test_menubar_cocoa.py
git commit -m "feat(ui): CocoaMenubarBackend con NSStatusItem + NSMenu + SF Symbols

Usa AppKit directo (sin rumps). Construye el menú con la estructura
del spec, arranca NSApp.run() en hilo daemon y refresca desde un
NSTimer a refresh_hz Hz leyendo _SharedState.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Create `client/requirements.txt` (missing file) + `requirements-macos.txt`

**Files:**
- Create: `client/requirements.txt`
- Create: `requirements-macos.txt`
- Modify: `install/macos/03-venv.sh` (skip if file absent for backward compat)

**Interfaces:**
- `client/requirements.txt` pins the runtime Python deps: `sounddevice`, `numpy`, `pyyaml`.
- `requirements-macos.txt` pins `pyobjc-framework-Cocoa>=10.0`.

- [ ] **Step 1: Read `install/macos/03-venv.sh` to confirm the path**

Already known: `03-venv.sh` line 17 is `"$VENV_DIR/bin/pip" install -r "$REPO_DIR/client/requirements.txt"`. The file is referenced but missing — creating it.

- [ ] **Step 2: Create `client/requirements.txt`**

```
numpy>=1.24
sounddevice>=0.4.6
pyyaml>=6.0
```

- [ ] **Step 3: Create `requirements-macos.txt`**

```
pyobjc-framework-Cocoa>=10.0
```

- [ ] **Step 4: Update `install/macos/03-venv.sh` to also install macOS requirements**

Replace the block after the existing pip install with:

```sh
_info "Instalando requirements específicos de macOS si existen…"
if [ -f "$REPO_DIR/requirements-macos.txt" ]; then
    "$VENV_DIR/bin/pip" install -r "$REPO_DIR/requirements-macos.txt"
fi
```

Final shape of the script:
```sh
_info "Actualizando pip e instalando requirements…"
"$VENV_DIR/bin/pip" install --upgrade pip wheel setuptools
"$VENV_DIR/bin/pip" install -r "$REPO_DIR/client/requirements.txt"
"$VENV_DIR/bin/pip" install sounddevice numpy
_info "Instalando requirements específicos de macOS si existen…"
if [ -f "$REPO_DIR/requirements-macos.txt" ]; then
    "$VENV_DIR/bin/pip" install -r "$REPO_DIR/requirements-macos.txt"
fi
_ok "Dependencias Python instaladas"
```

Note: the existing `pip install sounddevice numpy` is kept (it's idempotent given the requirements file also pins them). Optionally clean it up; for now the spec said "minimal changes" so keep.

- [ ] **Step 5: Verify install is idempotent**

Run: `bash install/macos/03-venv.sh`
Expected: ends with `_ok "Dependencias Python instaladas"`. No errors. Re-run produces the same outcome (idempotente).

- [ ] **Step 6: Commit**

```bash
cd /Users/alfonsogarre/Workspace/jota-voice
git add client/requirements.txt requirements-macos.txt install/macos/03-venv.sh
git commit -m "chore(deps): crear client/requirements.txt + requirements-macos.txt

El venv de macOS referencia client/requirements.txt pero el archivo
no existía. Ahora existe con sounddevice/numpy/pyyaml.

requirements-macos.txt añade pyobjc-framework-Cocoa>=10.0 y se instala
sólo si el fichero existe (no rompe Termux).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Wire menubar into `voice_client.py` + pausable OWW loop

**Files:**
- Modify: `client/app/voice_client.py`

**Interfaces:**
- After `display_task = asyncio.create_task(display.run(bus), name="display")`, add:
  - `menubar_backend = registry.make_menubar(cfg)`
  - `menubar_client = MenubarClient(menubar_backend)`
  - `ui_queue: queue.Queue = queue.Queue()`
  - `pause_event = asyncio.Event()`
  - `stop_event_holder = {"stop": stop_event}` (closure carrier)
  - Define `_open_logs`, `_open_config`, `_shutdown_service`, `_quit` closures (see Step 1).
  - Define `_oww_loop(audio)` function (see Step 1).
  - Build `MenubarCommands` and call `backend.set_commands(cmds)`.
  - If backend has `start` (i.e. is `CocoaMenubarBackend`): call `backend.start()`. Track backend for teardown.
  - `menubar_task = asyncio.create_task(menubar_client.run(bus, ui_queue, pause_event), name="menubar")`
  - Replace the existing `oww_task = asyncio.create_task(oww.run_forever(...))` with `oww_task = asyncio.create_task(_oww_loop(...))`.
- In the `finally` block: `menubar_task.cancel()` + await + if backend has `stop`: `backend.stop()`.

- [ ] **Step 1: Modify `voice_client.py`**

Read the file. The relevant block is the part starting from `# --- Task background permanente: OWW`. Replace the section from line ~138 (after `display_task`) to the end of the `try/finally` block. Apply these edits:

a) Add imports at the top (after the existing app imports, line ~87):

```python
import queue
import subprocess

from ui.menubar_base import MenubarCommands
from ui.menubar_client import MenubarClient
```

b) After `display_task = asyncio.create_task(display.run(bus), name="display")`, add:

```python
    # --- Task background permanente: Menubar UI (macOS) ---
    menubar_backend = registry.make_menubar(cfg)
    menubar_client = MenubarClient(menubar_backend)
    ui_queue: queue.Queue = queue.Queue()
    pause_event = asyncio.Event()

    def _open_logs() -> None:
        import platform
        path = cfg.menubar.log_path or os.path.expanduser(
            "~/Library/Logs/jota-voice/stdout.log"
        )
        try:
            if platform.system() == "Darwin":
                subprocess.Popen(["open", "-a", "Console", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            log.warning("Menubar: no se pudo abrir logs (%s): %s", path, exc)

    def _open_config() -> None:
        import platform
        path = cfg.menubar.config_path or os.path.expanduser(
            "~/Library/Application Support/jota-voice/config.yaml"
        )
        try:
            if platform.system() == "Darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            log.warning("Menubar: no se pudo abrir config (%s): %s", path, exc)

    def _shutdown_service() -> None:
        uid = os.getuid()
        try:
            subprocess.run(
                ["launchctl", "disable", f"gui/{uid}/com.jota.voice"],
                check=False, timeout=5,
            )
            subprocess.run(
                ["launchctl", "bootout", f"gui/{uid}/com.jota.voice"],
                check=False, timeout=5,
            )
        except Exception as exc:
            log.warning("Menubar: launchctl falló: %s", exc)
        stop_event.set()

    def _quit() -> None:
        stop_event.set()

    cmds = MenubarCommands(
        on_toggle_pause=lambda: ui_queue.put_nowait("toggle_pause"),
        on_open_logs=_open_logs,
        on_open_config=_open_config,
        on_shutdown_service=_shutdown_service,
        on_quit=_quit,
    )
    menubar_backend.set_commands(cmds)
    if hasattr(menubar_backend, "start"):
        menubar_backend.start()

    menubar_task = asyncio.create_task(
        menubar_client.run(bus, ui_queue, pause_event), name="menubar"
    )
```

c) Replace the existing OWW task creation:

BEFORE:
```python
    # --- Task background permanente: OWW (detección persistente de wake word) ---
    oww_task = asyncio.create_task(
        oww.run_forever(audio, _oww_on_wake), name="oww_listener"
    )
```

AFTER:
```python
    # --- Task background permanente: OWW (pausable) ---
    async def _oww_loop() -> None:
        while True:
            if pause_event.is_set():
                await asyncio.sleep(0.2)
                continue
            t = asyncio.create_task(oww.run_forever(audio, _oww_on_wake))
            pause_wait = asyncio.create_task(pause_event.wait())
            done, pending = await asyncio.wait(
                [t, pause_wait],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for p in pending:
                p.cancel()
                try:
                    await p
                except asyncio.CancelledError:
                    pass
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

    oww_task = asyncio.create_task(_oww_loop(), name="oww_listener")
```

d) In the `finally` block, add menubar cleanup BEFORE the existing teardown:

```python
    finally:
        log.info("Apagando jota-voice…")

        menubar_task.cancel()
        try:
            await menubar_task
        except asyncio.CancelledError:
            pass

        if hasattr(menubar_backend, "stop"):
            menubar_backend.stop()

        sm_task.cancel()
        # ...resto del bloque finally igual que antes
```

- [ ] **Step 2: Verify imports compile**

Run: `cd /Users/alfonsogarre/Workspace/jota-voice/client && PYTHONPATH=. python -c "from app.voice_client import main; print('OK')"`
Expected: prints `OK`. (If pyobjc is missing on Mac, the `make_menubar` call returns `NullMenubarBackend`, which has no `start`/`stop`, so this still passes.)

- [ ] **Step 3: Run the full test suite**

Run: `cd /Users/alfonsogarre/Workspace/jota-voice/client && PYTHONPATH=. pytest ../client/tests -v`
Expected: all existing tests + all new tests pass. No regressions.

- [ ] **Step 4: Commit**

```bash
cd /Users/alfonsogarre/Workspace/jota-voice
git add client/app/voice_client.py
git commit -m "feat(app): wire menubar UI y OWW pausable en voice_client

make_menubar se invoca en el arranque (null en Linux/Windows, Cocoa
en darwin). El OWW task se envuelve en _oww_loop() que observa
pause_event para cancelarse y recrearse cuando el toggle cambia.

launchctl disable antes de bootout evita el respawn por KeepAlive.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Update `config.example.yaml` and `README.md`

**Files:**
- Modify: `config.example.yaml`
- Modify: `README.md`

- [ ] **Step 1: Add commented menubar section to `config.example.yaml`**

Read the file, then append after the `control:` block (line ~57):

```yaml
# menubar:
#   enabled: true                # false desactiva el UI nativo (macOS)
#   refresh_hz: 5.0              # Frecuencia del repaint; clamp [1.0, 30.0]
#   log_path: null               # null = ~/Library/Logs/jota-voice/stdout.log
#   config_path: null            # null = ~/Library/Application Support/jota-voice/config.yaml
#
# Equivalente: export JOTA_DISABLE_MENUBAR=1 antes de arrancar el cliente.
```

- [ ] **Step 2: Add "macOS menubar" subsection to `README.md`**

Read the existing README. After the line containing `> **Nota sobre Wyoming OpenWakeWord:**` (around line 63), insert:

```markdown

### Barra de menú nativa

Al instalar jota-voice en macOS aparece automáticamente un icono en la barra de menús superior. El icono cambia según el estado del cliente:

| Estado | Icono (SF Symbol) | Significado |
|---|---|---|
| `idle` | `mic` | Esperando wake word |
| `listening` | `ear` | Grabando |
| `thinking` | `brain` | Esperando respuesta del gateway |
| `speaking` | `speaker.wave.2` | Reproduciendo TTS |
| `error` | `exclamationmark.triangle` | Último turno terminó en error |

El menú incluye:

- Cabecera con el estado actual (no seleccionable).
- Submenú **Servicio** → Pausar/Reanudar escucha, Apagar servicio.
- Abrir logs / Abrir configuración en la app por defecto.
- Acerca de / Salir.

Para desactivar el UI sin desinstalar pyobjc: `export JOTA_DISABLE_MENUBAR=1` antes de arrancar el cliente (o añade esa línea a tu `.jota-voice.env`).
```

- [ ] **Step 3: Verify YAML and Markdown parse**

Run: `cd /Users/alfonsogarre/Workspace/jota-voice && python -c "import yaml; yaml.safe_load(open('config.example.yaml')); print('YAML OK')"`
Expected: `YAML OK`.

Run: `cd /Users/alfonsogarre/Workspace/jota-voice && python -c "import re; re.search(r'^### Barra de menú nativa$', open('README.md').read(), re.M); print('README OK')"`
Expected: `README OK`.

- [ ] **Step 4: Commit**

```bash
cd /Users/alfonsogarre/Workspace/jota-voice
git add config.example.yaml README.md
git commit -m "docs: documentar sección menubar en YAML ejemplo y README

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Final verification

**Files:** none (read-only verification)

- [ ] **Step 1: Full test suite passes**

Run: `cd /Users/alfonsogarre/Workspace/jota-voice/client && PYTHONPATH=. pytest ../client/tests -v`
Expected: all tests pass. Cocoa tests skip on non-darwin.

- [ ] **Step 2: Smoke test still passes**

Run: `cd /Users/alfonsogarre/Workspace/jota-voice && bash install/shared/99-smoke-test.sh`
Expected: smoke test exits 0.

- [ ] **Step 3: Voice client imports without errors on the current platform**

Run: `cd /Users/alfonsogarre/Workspace/jota-voice/client && PYTHONPATH=. python -c "from app.voice_client import main; print('imports OK')"`
Expected: `imports OK`.

- [ ] **Step 4: Verify no new top-level dependency on non-macOS**

Run: `cd /Users/alfonsogarre/Workspace/jota-voice && grep -rn "pyobjc" client/ --include='*.py' --include='*.txt' --include='*.toml' | grep -v 'menubar_cocoa\\|requirements-macos\\|test_menubar_cocoa'`
Expected: no output (pyobjc is only referenced in `menubar_cocoa.py`, `test_menubar_cocoa.py`, and `requirements-macos.txt`, which is the macOS-only file).

- [ ] **Step 5: Manual smoke checklist (Mac only — out of agent scope; mark as user action)**

Verify on a Mac:

- `bash install/macos/install.sh` succeeds.
- `tail -f ~/Library/Logs/jota-voice/stdout.log` shows `Menubar` initialisation.
- The menubar icon appears at the top.
- Saying "ok_nabu" makes the icon transition through `listening → thinking → speaking → idle`.
- Clicking "Pausar escucha" makes subsequent "ok_nabu" utterances not trigger anything; "Reanudar escucha" restores it.
- "Abrir logs" opens Console.app.
- "Abrir configuración" opens the config in the default editor.
- "Apagar servicio" stops the launchd job (`launchctl list | grep com.jota.voice` returns nothing).
- "Salir" exits the client cleanly.

- [ ] **Step 6: Final commit if any doc fixups were needed**

```bash
cd /Users/alfonsogarre/Workspace/jota-voice
git status
# If clean, no commit. If not, commit fixups:
# git add -A && git commit -m "chore: smoke-test fixups"
```

---

## Self-Review Checklist

**Spec coverage:**

| Spec section | Implementing task(s) |
|---|---|
| `client/ui/` package + 4 modules | T2, T3, T4, T5, T7 |
| `_SharedState` thread-safe | T3 |
| `MenubarBackend` Protocol with 5 methods | T3, T4, T7 |
| `MenubarCommands` with 5 callbacks | T3, T9 |
| `NullMenubarBackend` no-op | T4 |
| `CocoaMenubarBackend` (NSStatusItem, NSMenu, SF Symbols, NSTimer @ refresh_hz) | T7 |
| `MenubarClient` (bus subscriber + queue drainer) | T5 |
| `make_menubar(cfg)` factory | T6 |
| `MenubarConfig` dataclass + `JOTA_DISABLE_MENUBAR` | T1 |
| OWW pausable via `_oww_loop` + `pause_event` | T9 |
| `launchctl disable` before `bootout` | T9 |
| `requirements-macos.txt` + `03-venv.sh` update | T8 |
| `client/requirements.txt` (missing file created) | T8 |
| `config.example.yaml` menubar section | T10 |
| README menubar subsection | T10 |
| Tests: `test_menubar_base` (lock, Protocol conformance) | T3 |
| Tests: `test_menubar_null` | T4 |
| Tests: `test_menubar_client` (event mapping + queue) | T5 |
| Tests: `test_menubar_cocoa` (darwin-only) | T7 |
| Tests: `test_registry` extension | T6 |
| Tests: `test_config` extension | T1 |
| Final verification | T11 |

**Placeholder scan:** No TBD/TODO/implement-later/fill-in-details. Every step has actual code or an actual command with expected output.

**Type consistency check:**
- `MenubarBackend.set_state/set_status_text/set_listening_paused/set_errors_count/set_commands` — defined T3, implemented T4 (no-op), T7 (Cocoa). All consistent.
- `MenubarCommands` fields: `on_toggle_pause`, `on_open_logs`, `on_open_config`, `on_shutdown_service`, `on_quit`. Used in T3 (test), T7 (`togglePause_/...` selectors), T9 (closure construction). All match.
- `_SharedState.read() -> tuple[str, str, int, bool]`. Used T3 (test), T7 (Cocoa tick). Match.
- `_SharedState.update(state=None, last_text=None, errors_count=None, listening_paused=None)`. Used T5 (via test of `set_state`), T7. Match.
- `MenubarClient.run(bus, ui_queue, pause_event)`. Signature matches T5 spec and T9 call site. Match.
- `make_menubar(cfg)`. Signature matches T6 spec and T9 call site. Match.
- `MenubarConfig.enabled/refresh_hz/log_path/config_path`. Defined T1, used in T6 (`cfg.menubar.enabled`), T9 (closures referencing `cfg.menubar.log_path/config_path`). Match.
- `pause_event: asyncio.Event` in `voice_client.py` (T9) consumed by `_oww_loop` (T9) and toggled by `MenubarClient` (T5). Match.

No issues found. Plan is internally consistent.