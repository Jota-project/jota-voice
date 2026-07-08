# macOS menubar UI for jota-voice

**Status:** design — awaiting user approval
**Date:** 2026-07-09
**Branch:** feature/universal-client
**Scope:** one PR. Bug hunting + general enhancements are explicitly out of scope.

## Context

`jota-voice` v2 is platform-agnostic: a single Python codebase runs on Termux (Android) and macOS via the `backends/` reorg (`audio_sounddevice.py` for Mac, `audio_termux.py` for Android, `platform_detect.py` for selection). The audio-to-audio pipeline works end-to-end in both environments: wake word → RECORDING → RESPONDING → TTS playback.

On macOS there is currently no way to interact with the running client except through `launchctl` and `tail -f ~/Library/Logs/jota-voice/stdout.log`. The user wants a native macOS menubar item that exposes state, lets them pause/resume the wake-word listener, opens logs and config in default apps, and shuts down the service. No kiosk launching, no diagnostic windows, no packaging into a `.app` — those are explicitly out of scope.

## Goal

Add a native macOS menubar UI to `jota-voice` that:

1. Lives in the same process as the voice client (no IPC, no second app, no HTTP polling).
2. Reflects state from the existing `EventBus` (the source of truth stays where it is).
3. Provides a small set of actions: pause/resume wake-word listening, open logs, open config, shutdown service, quit.
4. Follows the same hexagonal pattern as the rest of the codebase: a `ui/` layer parallel to `backends/`, with a Protocol and platform-specific implementations.
5. Is a no-op (not an error) on non-macOS platforms and in tests.

## Architecture

### New layer: `client/ui/`

```
client/
  ui/
    __init__.py
    menubar_base.py      # Protocol MenubarBackend + _SharedState + MenubarCommands
    menubar_cocoa.py     # pyobjc/AppKit implementation (macOS only)
    menubar_null.py      # no-op (Linux, Termux, Windows, tests)
    menubar_client.py    # EventBus subscriber, parallel to DisplayClient
```

The `registry` gets a new factory: `make_menubar(cfg)` that returns `CocoaMenubarBackend` on darwin (when pyobjc is available), `NullMenubarBackend` otherwise.

### Two threads, one process

macOS requires Cocoa for `NSStatusItem`, and Cocoa owns its own runloop (`NSApp.run()`). The existing code is asyncio. They share one process via two threads:

- **Main thread (Cocoa):** `NSApplication.run()`. Blocks this thread. Owns the `NSStatusItem` and its menu.
- **Worker thread (asyncio):** `asyncio.run(main_async())`. Owns `EventBus`, state machine, OWW, gateway, control server, display client, and the new menubar client.

Both threads are alive for the lifetime of the process. The worker thread is `daemon=True` so a Cocoa crash does not prevent process exit; the Cocoa thread is the OS main thread so it cannot be daemon.

### Inter-thread communication

**asyncio → UI (state projection):**
The menubar client (asyncio) updates a thread-safe `_SharedState` object (a dataclass guarded by `threading.Lock`). An `NSTimer` on the Cocoa thread reads it every 200 ms and repaints the menubar item. 5 Hz is more than enough for a menubar icon — no need for push notifications.

**UI → asyncio (commands):**
Menu item callbacks push strings onto a thread-safe `queue.Queue` (e.g. `"toggle_pause"`, `"open_logs"`, `"open_config"`, `"quit"`, `"shutdown_service"`). The menubar client's async loop drains this queue with `asyncio.run_in_executor` and acts on the events. The UI never writes to the bus; the bus is single-writer (asyncio only).

This is the same pattern as the existing `control_server.py` (HTTP `POST /cancel` sets an `asyncio.Event`), extended to a generic command queue.

### OWW task becomes pausable

Today `voice_client.py` creates the OWW task once and lets it run forever. The pause toggle requires starting/stopping it on demand. Refactor: wrap OWW in an `_oww_loop()` coroutine that watches a `pause_event` and cancels + recreates the OWW task when it toggles, using the same `asyncio.wait(..., return_when=FIRST_COMPLETED)` pattern already used in `_wait_wake_or_cancel` and `_recording`.

If a wake word is detected during the brief window between pause and cancel, the IDLE state machine ignores it (the wake word is consumed only when the state machine re-enters IDLE; OWW is not publishing into the bus while paused).

## Components

### `menubar_base.py`

```python
class MenubarBackend(Protocol):
    def set_state(self, state: str) -> None: ...
    def set_status_text(self, text: str) -> None: ...
    def set_listening_paused(self, paused: bool) -> None: ...
    def set_errors_count(self, n: int) -> None: ...
    def set_commands(self, cmds: "MenubarCommands") -> None: ...

@dataclass
class MenubarCommands:
    on_toggle_pause: Callable[[], None]
    on_open_logs: Callable[[], None]
    on_open_config: Callable[[], None]
    on_shutdown_service: Callable[[], None]
    on_quit: Callable[[], None]

@dataclass
class _SharedState:
    state: str = "idle"
    last_text: str = ""
    errors_count: int = 0
    listening_paused: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)
```

The Protocol is `runtime_checkable` so the test in `test_menubar_base.py` can verify implementations satisfy it without importing Cocoa.

### `menubar_cocoa.py`

- `NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)`.
- SF Symbols icons mapped to state:
  - `idle` → `mic`
  - `listening` → `ear`
  - `thinking` → `brain`
  - `speaking` → `speaker.wave.2`
  - `error` → `exclamationmark.triangle`
- Title text = readable state (`"IDLE"`, `"LISTENING"`, `"THINKING"`, `"SPEAKING"`).
- `NSMenu` structure (in this exact order):
  1. Header item (non-selectable): icon + current state + listening-paused badge.
  2. **Submenu "Servicio"** containing:
     - "Pausar escucha" / "Reanudar escucha" (label flips based on `_SharedState.listening_paused`).
     - "Apagar servicio" → invokes `on_shutdown_service`.
  3. Separator.
  4. "Abrir logs" → invokes `on_open_logs`.
  5. "Abrir configuración" → invokes `on_open_config`.
  6. Separator.
  7. "Acerca de jota-voice" → `NSAlert` showing version (read from `__version__` or `pyproject.toml`).
  8. "Salir" → invokes `on_quit`.
- `NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(0.2, ...)` reads `_SharedState` and repaints icon, title, and the pause/resume label.
- The Cocoa thread invokes `NSApplication.sharedApplication().run()` from a `threading.Thread(target=..., daemon=True).start()` started in `voice_client.main()`.

### `menubar_null.py`

A class whose methods all log at DEBUG and return `None`. Used on Linux/Termux/Windows and when `pyobjc` is missing. Satisfies the Protocol so the rest of the code does not branch.

### `menubar_client.py`

Parallel to `DisplayClient`:

```python
class MenubarClient:
    def __init__(self, backend, cfg) -> None: ...
    async def run(self, bus, ui_queue, pause_event) -> None: ...
```

- Subscribes to `bus`. On each event:
  - `state_changed` → `backend.set_state(event.data["state"])`.
  - `transcription` → `backend.set_status_text(event.data["text"])`.
  - `error` → increment internal counter, `backend.set_errors_count(n)`.
- Runs a second async loop draining `ui_queue` (via `asyncio.run_in_executor` on a `queue.Queue`) and:
  - `"toggle_pause"` → flip `pause_event`, then `backend.set_listening_paused(pause_event.is_set())`.
  - `"open_logs"` → invoke the configured action (default: `subprocess.Popen(["open", "-a", "Console", LOG_PATH])`).
  - `"open_config"` → invoke the configured action (default: `subprocess.Popen(["open", CONFIG_PATH])`).
  - `"shutdown_service"` → `subprocess.run(["launchctl", "disable", f"gui/{uid}/com.jota.voice"], check=False)` then `subprocess.run(["launchctl", "bootout", f"gui/{uid}/com.jota.voice"], check=False)`. The `disable` runs first to prevent launchd from auto-respawning the agent (`KeepAlive=true` would otherwise restart it). Whether or not the bootout succeeded, the local client exits via `stop_event.set()` so the user always gets feedback.
  - `"quit"` → `stop_event.set()` (the same event SIGTERM/SIGINT set, reused so the existing shutdown path runs).

### `registry.py` addition

```python
def make_menubar(cfg):
    if not cfg.menubar.enabled:
        return NullMenubarBackend()
    if sys.platform == "darwin":
        try:
            from .menubar_cocoa import CocoaMenubarBackend
            return CocoaMenubarBackend(cfg.menubar)
        except ImportError:
            log.warning("pyobjc no disponible; menubar UI desactivada")
            return NullMenubarBackend()
    return NullMenubarBackend()
```

The `enabled` flag defaults to `True`. The environment variable `JOTA_DISABLE_MENUBAR=1` forces it to `False` so tests and headless setups can opt out.

### `voice_client.py` changes

- Import and call `registry.make_menubar(cfg)`.
- Build a `MenubarCommands` with closures over the existing `stop_event`, the new `pause_event`, and `subprocess` calls.
- Pass those commands to `backend.set_commands(...)` once after the backend is constructed.
- Wrap OWW in `_oww_loop(pause_event)`:
  ```python
  async def _oww_loop():
      while True:
          if pause_event.is_set():
              await asyncio.sleep(0.2)
              continue
          oww_task = asyncio.create_task(oww.run_forever(audio, _oww_on_wake))
          pause_wait = asyncio.create_task(pause_event.wait())
          done, pending = await asyncio.wait(
              [oww_task, pause_wait],
              return_when=asyncio.FIRST_COMPLETED,
          )
          # Cancel the loser, then oww_task regardless (the loop may have completed
          # naturally and we still want to restart it after a pause toggle).
          for p in pending:
              p.cancel()
              try: await p
              except asyncio.CancelledError: pass
          oww_task.cancel()
          try: await oww_task
          except asyncio.CancelledError: pass
  ```
  This is the same `asyncio.wait(..., return_when=FIRST_COMPLETED)` shape used in `_wait_wake_or_cancel` and `_recording`. The loop is restart-safe: cancelling `pause_wait` while `oww_task` is still running only cancels the wait; cancelling `oww_task` only cancels OWW.
- Start the menubar client as a background task alongside the existing display client.
- In the `finally` block: `menubar_task.cancel()`, `NSApp.terminate_(None)` via `performSelectorOnMainThread_withObject_waitUntilDone_`, then continue with the existing teardown.

### `config.py` addition

```python
class MenubarConfig(BaseModel):
    enabled: bool = True
    refresh_hz: float = 5.0
    log_path: Optional[str] = None      # default: ~/Library/Logs/jota-voice/stdout.log
    config_path: Optional[str] = None   # default: ~/Library/Application Support/jota-voice/config.yaml
```

`refresh_hz` is clamped to `[1.0, 30.0]`; values outside the range are rejected by Pydantic. The CLI override `JOTA_DISABLE_MENUBAR=1` is read in `load_config` and overrides `enabled`.

## Data flow

```
[EventBus] ──subscribe──► MenubarClient ──set_state/set_status_text/set_errors_count──► [CocoaMenubarBackend]
                                                                                            │
                                                                                            ▼
[NSTimer @ 5Hz] ──read──► _SharedState ◄──lock── MenubarClient ◄──ui_queue◄── [NSMenu callbacks]
                                                          │
                                                          └──toggle pause_event──► [_oww_loop]
                                                                                            │
                                                                                            ▼
                                                                                  cancel/recreate oww_task
```

## Lifecycle

### Startup

1. `_setup_logging`.
2. Build `EventBus`.
3. Build audio, oww, display backend, gateway, playback, display client.
4. **New:** if darwin and pyobjc available, construct `_SharedState`, `CocoaMenubarBackend(shared_state, cfg.menubar)`. Start a daemon thread running `NSApplication.sharedApplication().run()`. Construct `MenubarCommands` closures over `stop_event`, `pause_event`, and the file paths.
5. **New:** build `MenubarClient(backend, cfg.menubar)`, register the commands on the backend, then `menubar_task = asyncio.create_task(menubar_client.run(bus, ui_queue, pause_event))`.
6. Start `audio.start()`.
7. Replace `oww_task = asyncio.create_task(oww.run_forever(...))` with `oww_task = asyncio.create_task(_oww_loop(pause_event))`.
8. Continue with display client, control server, state machine as today.

### Shutdown

1. SIGTERM/SIGINT, the menubar "Salir" item, or `launchctl bootout` from "Apagar servicio" sets `stop_event`.
2. `asyncio.wait` returns. `voice_client.main()` enters `finally`.
3. **New:** `menubar_task.cancel()` and `await asyncio.gather(..., return_exceptions=True)`.
4. **New:** `NSApp.sharedApplication().terminate_(None)` via `performSelectorOnMainThread_withObject_waitUntilDone_(False)`.
5. Continue with the existing teardown: cancel all tasks, `audio.stop()`, `gateway.disconnect()`, `oww.disconnect()`, `bus.close()`.

## Error handling

| Failure | Effect | Recovery |
|---|---|---|
| `pyobjc-framework-Cocoa` not installed on Mac | `ImportError` when constructing `CocoaMenubarBackend` | `make_menubar` returns `NullMenubarBackend`, logs WARNING with the pip install command. Voice client runs normally without UI. |
| `NSApp.run()` fails (no display server) | Exception in Cocoa thread | Thread is daemon; asyncio continues without UI; WARNING at log. |
| `launchctl bootout` fails in "Apagar servicio" (e.g. service not loaded) | `CalledProcessError` from subprocess | Both `launchctl disable` and `launchctl bootout` run with `check=False`; the WARNING is logged but `stop_event.set()` still fires so the client always exits. |
| `open -a Console` fails (Console.app moved) | `OSError` | Log WARNING; the menu item does not block. |
| Pause toggled mid wake-word | OWW task cancelled before publishing | IDLE state machine discards stale wake-word events because pause is checked on re-entry. No partial state. |
| `pyobjc` import succeeds at install time but fails at runtime (e.g. venv mismatch) | `ImportError` in `CocoaMenubarBackend.__init__` | Same fallback path as missing pyobjc. |

## Testing

### `client/tests/ui/test_menubar_base.py`

- Verify `NullMenubarBackend` satisfies the Protocol via `runtime_checkable` isinstance check.
- Verify all `NullMenubarBackend` methods are no-ops (`set_state`, `set_status_text`, `set_listening_paused`, `set_errors_count`, `set_commands`).
- Verify `_SharedState` round-trips through its lock without deadlock (1000 read/write cycles from two threads).

### `client/tests/ui/test_menubar_client.py`

No Cocoa dependency. Uses a fake backend object whose methods append to a list.

- `state_changed({"state": "listening"})` → fake backend receives `set_state("listening")` exactly once.
- `transcription({"text": "hola"})` → fake backend receives `set_status_text("hola")`.
- `error({"message": "x"})` → fake backend receives `set_errors_count(1)`, then again with `2` after a second error.
- Putting `"toggle_pause"` on `ui_queue` → pause_event is set; another `"toggle_pause"` clears it; `set_listening_paused` is called with the matching value on each flip.
- `bus.close()` causes `menubar_client.run()` to return without raising.

### `client/tests/ui/test_menubar_cocoa.py`

Skipped unless `sys.platform == "darwin"` and `pyobjc` is importable. Constructs a real `CocoaMenubarBackend` and pumps the NSApp runloop for a short window. Verifies:

- Setting `_SharedState.state = "listening"` and waiting one timer tick causes the status item title to contain `"LISTENING"`.
- Setting `_SharedState.listening_paused = True` causes the menu's pause item label to become `"Reanudar escucha"`.

### `client/tests/backends/test_registry.py` (extend)

- `make_menubar(cfg)` returns `NullMenubarBackend` on linux regardless of cfg.
- `make_menubar(cfg)` returns `NullMenubarBackend` on darwin when pyobjc import fails (monkeypatch `menubar_cocoa` import to raise).
- `make_menubar(cfg)` returns `CocoaMenubarBackend` on darwin when pyobjc is available (skip if not).

## Installation changes

### `requirements-macos.txt` (new)

```
pyobjc-framework-Cocoa>=10.0
```

### `install/macos/03-venv.sh`

After the existing `pip install -r requirements.txt`, add:

```sh
if [ -f "$REPO_DIR/requirements-macos.txt" ]; then
    "$VENV_DIR/bin/pip" install -r "$REPO_DIR/requirements-macos.txt"
fi
```

### `install/macos/07-launchd.sh`

No changes. The launchd plist still runs `python3 voice_client.py config.yaml`; the menubar UI starts inside that process.

### `install/macos/02-config-wizard.sh`, `04-oww.sh`, `06-configs.sh`

No changes.

### `README.md`

Add a "macOS menubar" subsection under the macOS install instructions explaining:

- The menubar item appears automatically after install.
- Icon meaning per state.
- What each menu item does.
- How to disable it (`JOTA_DISABLE_MENUBAR=1` in `~/.jota-voice.env` or `launchctl setenv`).

## Out of scope (explicit)

- Bug hunting and general enhancements to the audio pipeline.
- Launching the kiosk display from the menu.
- A diagnostic window showing the last N bus events.
- Packaging into a `.app` (py2app, pyinstaller).
- Migrating the menubar UI to Swift/Objective-C.
- Persisting "paused" state across restarts. The client always starts listening on launch; if the user wants it paused, they click the menu after startup.
- Localising menu labels (Spanish only for now).
- Global keyboard shortcuts (e.g. ⌘⇧J to toggle pause).

## Done criteria

- `pytest -q` passes on macOS including the Cocoa tests.
- `pytest -q` passes on Linux with the Cocoa tests skipped.
- `install/shared/99-smoke-test.sh` still passes.
- Manual smoke on a Mac: after `bash install/macos/install.sh`, the menubar icon appears. Saying "ok_nabu" causes the icon to change to the listening/responding states. The toggle pauses OWW (verified by saying "ok_nabu" while paused: nothing happens). "Apagar servicio" stops launchd. "Salir" exits cleanly.