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
    def set_health(self, level: str, text: str) -> None:
        """Estado del sistema fuera del turno en curso (gateway, servicios,
        wake word). ``level`` es uno de los ``HEALTH_*``; con
        ``HEALTH_OK`` el texto es "" y el aviso se oculta."""
        ...


# Niveles de set_health(), de menos a más grave.
HEALTH_OK = "ok"        # nada que avisar
HEALTH_INFO = "info"    # aviso discreto (p.ej. transcripción degradada)
HEALTH_WARN = "warn"    # algo no funciona pero se recupera solo
HEALTH_FATAL = "fatal"  # requiere acción del usuario (client_key inválida…)


@runtime_checkable
class BlockingMenubarBackend(Protocol):
    """Contrato opcional para backends cuyo toolkit exige su propio bucle de
    eventos bloqueante en el hilo principal (Cocoa hoy; GTK/AppIndicator o
    Win32/pystray mañana).

    Si un backend lo cumple, el arranque (``voice_client``) invierte el
    reparto de hilos:

    - hilo principal real del proceso → ``run_forever()``, que bloquea
      hasta que alguien llame a ``stop()``;
    - hilo de trabajo → ``asyncio.run(main(...))``, con el EventBus, la
      state machine y el ``MenubarClient``.

    No al revés: construir el NSStatusItem en un hilo y correr
    ``NSApp.run()`` en otro distinto cuelga el proceso (comprobado). Un
    backend futuro debe respetar las mismas reglas:

    - ``run_forever()`` se llama UNA vez, desde el hilo principal, y no
      retorna hasta ``stop()``. Al retornar debe haber liberado sus
      recursos de UI.
    - ``stop()`` puede llamarse desde cualquier hilo (normalmente desde el
      hilo asyncio al apagar, o desde el propio hilo de UI con "Salir") y
      más de una vez. Lo que toque la UI lo despacha al hilo principal.
    - Los ``set_*`` de :class:`MenubarBackend` también llegan desde el hilo
      asyncio: no deben tocar widgets directamente, solo estado compartido
      (:class:`_SharedState`) y una petición de repintado al hilo de UI.
    """

    def run_forever(self) -> None: ...
    def stop(self) -> None: ...


def needs_main_thread(backend: object) -> bool:
    """True si ``backend`` necesita el hilo principal para su bucle de
    eventos (ver :class:`BlockingMenubarBackend`)."""
    return isinstance(backend, BlockingMenubarBackend)


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
    health_level: str = HEALTH_OK
    health_text: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def read(self) -> tuple[str, str, int, bool]:
        with self._lock:
            return (self.state, self.last_text, self.errors_count, self.listening_paused)

    def read_health(self) -> tuple[str, str]:
        with self._lock:
            return (self.health_level, self.health_text)

    def set_health(self, level: str, text: str) -> None:
        with self._lock:
            self.health_level = level
            self.health_text = text

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
