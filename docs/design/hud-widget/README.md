# HUD Widget — capa visual de J Voice en macOS

> **Estado: exploración.** Nada de esta carpeta es diseño cerrado ni especificación de implementación. Son maquetas de posibles diseños y decisiones en discusión. No implementar desde aquí sin confirmarlo antes.

Overlay flotante que muestra la conversación por voz sobre la pantalla, complementando el item de barra de menú que ya existe en este repo.

## Estructura

```
hud-widget/
├── PLAN.md          Decisiones en discusión y razonamiento
├── maquetas/        Propuestas visuales — exploración, no spec
├── figma/           Plugin que construye las propuestas en Figma (build inicial, congelado)
├── penpot/          Misma construcción migrada a Penpot (build activo)
└── referencia/      Cálculos que respaldan las decisiones
```

### `maquetas/` — propuestas, no acuerdos

| Maqueta | Propone | Estado |
|---|---|---|
| `01-vidrio-hud.html` | Vidrio oscuro tipo HUD, esquina superior derecha, conversación sin burbujas, tres estados | 🟡 en discusión |
| `02-flujo-demo.html` | El flujo completo animado y en bucle: escuchando → transcripción en vivo → pensando → tool call (running→done) → respuesta en streaming → tabla. Pensada para grabar como vídeo de producto. Controles de play/pause/reinicio y "modo captura" para ocultarlos antes de grabar | 🟡 en discusión |

Ábrelas en Safari a pantalla completa. `01` lleva controles abajo a la izquierda para cambiar fondo, estado y opacidad.

Cada maqueta es **una propuesta entre varias posibles**. Si una se descarta, se queda aquí documentada con su motivo en vez de borrarse — el historial de lo rechazado vale tanto como lo elegido.

### `figma/` — construcción en Figma

Plugin local que genera las propuestas como componentes y variantes reales en el archivo de Figma. No consume cuota del MCP. Instrucciones en `figma/README.md`.

Archivo: [`Jota-voice-mac-UI`](https://www.figma.com/design/Fj3TQHcQiKPNbgRc8NUxmK/Jota-voice-mac-UI) → páginas `JV · Components` y `JV · States`.

Lo que hay en Figma **refleja el estado de las maquetas**, no un diseño aprobado. El plugin es re-ejecutable: borra y rehace sus páginas en cada pasada.

**Este build está congelado** — el trabajo activo se movió a Penpot (ver abajo) por límites de cuota del MCP de Figma en el plan Starter.

### `penpot/` — construcción activa en Penpot

Mismos componentes y estados que el build de Figma (Orb, Turn, Tool Call, Table, Image, y los tres estados Pill/Glance/Panel), reconstruidos en Penpot con su propio Design Tokens API y su propio Plugin API. Incluye fondos de contexto (wallpaper, documento blanco, editor oscuro) con cada estado insertado de verdad para comprobar el vidrio en condiciones reales, y un prototipo de click-through entre los tres estados.

Archivo: **JOTA-VOICE-MAC → j-voice-mac**, página `Components`. Detalles, decisiones y trampas de la API específicas de Penpot en `penpot/README.md`.

### `referencia/` — lo que respalda las decisiones

`contrast-check.py` — cálculo WCAG del contraste real del texto sobre el vidrio compuesto, contra cuatro fondos. Es lo que fija la opacidad de la superficie. `python3 referencia/contrast-check.py`

---

## Dirección actual (sujeta a cambio)

**Material, no color.** La superficie es vidrio del sistema (`NSVisualEffectView` / `.hudWindow`), no un panel con la paleta de marca. El tinte del vidrio deriva de Ink `#2A2433`, así que la marca está presente pero es subliminal. Jota aparece explícitamente solo en el orbe y en los estados activos.

**Dark-first, siempre.** El HUD no sigue el tema del sistema. Como Spotlight o el HUD de volumen.

**Esquina superior derecha**, `top: 38px` (bajo la barra de menú), `right: 14px`. Flota libre, sin anclarse al icono de la barra.

**Sin burbujas de chat.** El texto flota sobre el vidrio. Tu turno recede (derecha, 13px, secundario); el de J domina (izquierda, 14.5px, primario). Los widgets ricos — tool calls, tablas, imágenes — pasan a ser los únicos anclajes visuales.

**Tres estados, no dos:**

```
pill (230px)      →  respuesta (340px)   →  panel (380px)
escuchando           respondió, se va       hay algo que explorar
                     a los ~6s
```

La respuesta efímera es la clave del "no invasivo": *"pon un temporizador"* no debería abrir nunca un panel completo. El panel es la excepción, no el destino por defecto.

## La opacidad al 72 % no es estética

Sale del cálculo de `referencia/contrast-check.py`. Sobre un documento blanco detrás del widget:

| Fill | Texto primario | Veredicto |
|---|---|---|
| 60 % | 4.2:1 | ❌ por debajo de AA |
| **72 %** | **6.2:1** | ✅ elegido |
| 84 % | 9.4:1 | cumple, pero ya no parece vidrio |

Si se toca ese valor, hay que recalcular. El fallo típico es calibrar el vidrio sobre un wallpaper bonito y que se rompa sobre un Google Docs.

## Abierto

- Validar la dirección de `01-vidrio-hud` antes de considerar nada de esto estable
- Animaciones entre estados en Figma (API tras el feature flag `metronome`; specs en `PLAN.md` §5, funcionando en la maqueta)
- Estado de error: tool call fallida, pérdida de conexión con el gateway
- Comportamiento multi-monitor y espacios (`canJoinAllSpaces`)
- Atajo de teclado (`⌘⇧J` es un placeholder de la maqueta)
- Relación con `jota-display`, que compartirá componentes con esta capa
