# Suite de diseño macOS — jota-voice

> **Estado: exploración.** Decisiones de producto ya tomadas (ver abajo), pero nada de la implementación visual está cerrado hasta pasar por las fases descritas aquí.

Esta carpeta es la única fuente de verdad del diseño de la app de macOS de jota-voice — el menú de la barra de estado, el HUD flotante opcional y la ventana de Preferencias. **Es diseño puro**: no decide en qué repositorio ni con qué stack se implementa nada. Esa decisión es aparte y posterior.

## Por qué vive aquí y no en `docs/superpowers/`

`docs/superpowers/` está excluido de git a propósito (`.gitignore`: *"Planes y specs de desarrollo, internos, no para el repo público"*) — es el espacio de trabajo interno para specs de código. Las decisiones de este documento nacieron ahí (sesión del 2026-08-02) pero se han traído aquí porque **el diseño sí necesita persistir**: si vive solo en `docs/superpowers/`, desaparece en un `git clone` fresco y no lo ve nadie más que revise el repo.

## Estructura

```
docs/design/
├── README.md          Este archivo — decisiones de producto y mapa de las 3 superficies
├── tokens/             Sistema de tokens Dusk (color, tipografía, espaciado) — W3C DTCG, Penpot-ready
├── hud-widget/          Panel flotante opcional: componentes, maquetas HTML, build en Penpot
└── preferences/         Ventana de Preferencias: IA, wireframes, (futuro) componentes de formulario
```

## Las tres superficies de macOS

| Superficie | Qué es | Estado |
|---|---|---|
| **Menú de la barra de estado** | Lo que ya existe hoy en `menubar_cocoa.py` — icono con 6 estados, submenú Servicio, logs, configuración. Se le añaden 2 entradas nuevas (abajo). | Implementado, sin cambios de fondo |
| **HUD flotante** | Panel opcional (Pill/Glance/Panel) con transcripción y respuesta en vivo. El usuario lo activa/desactiva desde el menú. | Componentes construidos en Penpot, ver `hud-widget/` |
| **Preferencias** | Ventana nativa con formularios que sustituye "abrir el YAML en el editor". | En diseño, ver `preferences/` |

## Decisiones de producto (tomadas el 2026-08-02)

| ID | Decisión |
|---|---|
| D1 | El HUD es una **capa opcional**, no sustituye el menú. Se activa desde una entrada nueva del menú: **"Mostrar panel flotante"**. |
| D2 | El Mac y el kiosk Android (`jota-display`) son **visualmente independientes** — no comparten sistema de diseño. |
| D3 | Sí hay **Preferencias reales**: formularios con validación, no un editor de texto. |
| D4 | Arquitectura (para cuando se implemente, no ahora): un `HudBackend` nuevo, paralelo al `MenubarBackend` existente, consumidor del mismo Event Bus. La Preferencias no usa el bus — lee/escribe el YAML directamente, como ya hace hoy el asistente de instalación. |
| D5 | La Preferencias es una ventana nativa del propio proceso (no un frontend nuevo tipo Electron/web). |
| D6 | El HUD no diseña nada que el sistema no pueda alimentar de verdad. Ver la tabla de contrato de datos en `hud-widget/`. |

## Las dos entradas nuevas del menú

Entre el submenú "Servicio" y "Abrir logs":

- **"Mostrar panel flotante"** (checkbox) — activa/desactiva el HUD.
- **"Preferencias…"** — abre la ventana nueva.

## Contrato de datos del HUD — qué es real y qué es ilustrativo

| Lo que el HUD muestra | Necesita | Estado real hoy |
|---|---|---|
| Transcripción en vivo mientras hablas (pill) | evento `transcription_partial` | Existe en el sistema, pero **nadie lo muestra en pantalla todavía** — haría falta un consumidor nuevo |
| Transcripción final ("Tú") | evento `transcription` | Ya se usa hoy (menú y kiosk) |
| Respuesta de J en streaming | evento `display_text_update` | **Ya existe y ya está sincronizado con el audio real** — el HUD debe usar esto tal cual, no inventar una velocidad de tecleo |
| Orbe con estado (idle/listening/thinking/speaking/error) | eventos ya usados por el menú actual | Ya existe |
| Tool calls ejecutándose / tablas de resultado | — | **No existe ningún evento para esto todavía.** Vendría de `jota-gateway`, no de `jota-voice`. Se queda como exploración ilustrativa hasta que exista ese dato real |

## Fases

1. **Este documento** — decisiones de producto y mapa de superficies. ✅
2. **IA y wireframes de Preferencias** — estructura y flujo, sin visual. ✅ Ver `preferences/`.
3. **Sistema de componentes de formulario en Penpot** — botón, campo, toggle, dropdown, slider, pestañas. ✅
4. **Preferencias en alta fidelidad** en Penpot. ✅ Las 4 pestañas construidas y verificadas.
5. **Cerrar el gap de datos del HUD** — ✅ El `HUD · Panel` se separó en dos boards en Penpot: `HUD · Panel — Real (hoy)` (Header + Turn User + Turn J, nada más — es la versión canónica, sin Tool Call ni Table) y `HUD · Panel — Exploración (tool calls futuros)` (la misma conversación con Tool Call + Table, envueltos en un recuadro discontinuo etiquetado `FUTURO · SIN DATOS REALES AÚN`). El texto de J en la versión Real ya no promete una tabla que no existe ahí. Detalle en `hud-widget/penpot/README.md`.
6. **Prototipo end-to-end** — ✅ Un único flujo click-through, en Penpot, página `03 · Screens`: menú de la barra de estado (`Flow · 01 Menu`, con las 2 entradas nuevas) → clic en "Mostrar panel flotante" → Pill → Listening → Thinking → Speaking → Glance → (a los 3s) vuelta al menú; y por otro lado, clic en "Preferencias…" → las 4 pestañas cruzadas entre sí → clic en el punto rojo del semáforo → vuelta al menú. Los 5 estados del HUD comparten el mismo fondo de escritorio y usan `Slide` entre ellos para fingir continuidad (no hay Smart Animate en Penpot); el cierre de cada ciclo usa `Dissolve`, que sí es un salto de contexto real. Detalle técnico y trampas del API en `hud-widget/penpot/README.md`.

## Abierto / a confirmar

- Qué campos de `config.yaml` van en la pestaña "Voz" de Preferencias y cuáles en "Avanzado" — propuesta de primer corte en `preferences/README.md`, pendiente de que la corrijas.
- Si "Iniciar con el Mac" en Preferencias controla el `launchd` agent directamente o eso se queda solo en el instalador.
- Si merece la pena adelantar el trabajo de protocolo en `jota-gateway` para que las tool calls sean reales, o se deja fuera de alcance por ahora.
