# J Voice · plan de rediseño del widget macOS

> **Estado: exploración.** Decisiones en discusión, no especificación cerrada. Ver `README.md`.

Sesión de planificación — 2 ago 2026. Ejecución en Figma: siguiente sesión.
Archivo: `Jota-voice-mac-UI` (`Fj3TQHcQiKPNbgRc8NUxmK`), página **J Voice — Widget**.

---

## 1. El diagnóstico

Lo que construimos aplica la paleta Dusk como si fuera una interfaz web: fondos Linen opacos, burbujas de color plano, bordes visibles en cada contenedor. En macOS eso se lee como una app extranjera flotando sobre el escritorio.

El cambio no es de color, es de **material**. La superficie tiene que ser vidrio del sistema, y la marca Jota debe aparecer solo como acento — el orbe, los estados activos, poco más. La identidad la aporta el comportamiento y el orbe, no el relleno.

### Las tres decisiones tomadas

| | |
|---|---|
| **Apariencia** | HUD oscuro siempre, independiente del tema del sistema (como Spotlight o el HUD de volumen). Una sola modalidad de variables. |
| **Anclaje** | Flota libre en la **esquina superior derecha**. Sin relación visual con el icono de la barra de menú. |
| **Conversación** | Texto flotante puro, sin burbujas. Jerarquía por alineación, tamaño y color. |

---

## 2. Receta del material

Verificada contra cuatro fondos con cálculo de contraste WCAG (ver §3).

```
Superficie HUD
  tinte        rgb(30, 27, 38)      ← derivado de Ink #2A2433
  opacidad     72 %
  blur         50 px
  saturación   180 %                ← lo que hace que el vidrio "recoja" el color del fondo
  borde        0.5 px  rgba(255,255,255,.10)
  luz superior inset 0 1px 0 rgba(255,255,255,.18)
  sombra       0 1px 3px rgba(0,0,0,.30)
               0 24px 60px -12px rgba(0,0,0,.55)
```

Dos detalles que separan el vidrio bien hecho del mal hecho:

- **La saturación al 180 %** es la mitad del efecto. Sin ella el blur se ve gris y muerto; con ella la superficie recoge el color de lo que hay debajo y se siente viva.
- **El tinte va derivado de Ink**, no negro neutro. Es la única contribución de la marca al material, y es subliminal — nadie la nombra, pero hace que el vidrio de J no sea el vidrio de cualquiera.

### En Figma

`backdrop-filter` se traduce a un efecto **Background Blur** de 50 px sobre un frame con fill `rgb(30,27,38)` al 72 %. La saturación no tiene equivalente nativo; se compensa subiendo ligeramente la saturación del tinte. Los frames de contexto detrás deben llevar wallpapers reales o el blur no se aprecia.

---

## 3. Legibilidad — verificado, no estimado

Calculé el contraste real del texto compuesto sobre el material, contra cuatro fondos. El caso duro es un documento blanco puro detrás del widget.

**Fill al 72 % (elegido):**

| Fondo | Primario .95 | Secundario .62 | Terciario .48 |
|---|---|---|---|
| Blanco puro | **6.2** ✓ | 3.7 | 2.9 |
| Documento gris claro | **6.7** ✓ | 3.9 | 3.0 |
| Wallpaper medio | **13.0** ✓ | 6.4 ✓ | 4.5 |
| Editor oscuro | **15.6** ✓ | 7.3 ✓ | 4.9 ✓ |

Por qué 72 % y no menos: **al 60 % el texto primario cae a 4.2 sobre blanco**, por debajo del mínimo AA. Es exactamente el fallo que te preocupaba — se ve bonito sobre el wallpaper del diseñador y se rompe sobre un Google Docs. Al 84 % cumple de sobra pero ya no parece vidrio, parece un panel gris.

**Ajuste sobre lo que probé primero:** subir secundario de `.58` a `.62` y terciario de `.36` a `.48`. El terciario original era ilegible sobre fondos claros. Con `.48` sigue siendo claramente terciario pero se sostiene.

El terciario nunca baja de 3.0 salvo sobre blanco absoluto — aceptable porque solo lo usamos para metadatos no críticos (captions, atajos, timestamps). Nunca para contenido que haya que leer.

**Pendiente de implementación:** respetar *Reducir transparencia* de Accesibilidad → fill sólido al 96 %, blur a 0.

---

## 4. Cambios estructurales

### 4.1 Posición

Esquina superior derecha. `top: 38px` (bajo la barra de menú), `right: 14px`.

### 4.2 Fuera las burbujas

El cambio que más "respiro" aporta. En vez de contenedores de color:

- **Tu turno** — alineado a la derecha, 13 px, color secundario. Recede: ya sabes lo que dijiste.
- **Turno de J** — alineado a la izquierda, 14.5 px, color primario. Domina: es la respuesta.

Los widgets ricos pasan a ser los **únicos anclajes visuales** del panel. Al no competir con burbujas, ganan presencia sin necesitar bordes gruesos.

Separación entre turnos: **20 px** (antes 14). El respiro se compra con espacio, no con transparencia.

### 4.3 Los widgets ricos, más ligeros

| | Antes | Ahora |
|---|---|---|
| **Tool call** | Tarjeta con borde, badge de color, icono en cuadro malva | Fila con glifo monocromo, nombre en monoespaciada, estado en terciario, barra de progreso de 1.5 px |
| **Tabla** | Filas con fondo alterno, cabecera en malva-tint | Solo hairlines a `rgba(255,255,255,.09)`, cabecera en versalitas terciarias, cifras tabulares alineadas a la derecha |
| **Imagen** | Tarjeta con borde y padding | Imagen a sangre con radio 13, caption suelto debajo |

Radio: 22 px el panel, 13 px los widgets internos (concéntrico aproximado).

### 4.4 Tipografía

**SF Pro Text / SF Pro Display**, no Inter. Inter era un sustituto razonable pero SF es lo que hace que una app se sienta nativa. Ya tienes los UI kits de macOS 26 y 27 vinculados al archivo, así que las escalas de texto están disponibles.

**Fraunces se queda fuera del widget.** Es tipografía de display de marca — perfecta en la web y el pitch deck, foránea en un HUD del sistema. La marca vive en el orbe.

Monoespaciada (SF Mono) para nombres de herramienta y argumentos: comunica "esto es una llamada real a una herramienta" sin necesitar decoración.

### 4.5 Estado nuevo: la respuesta efímera

Ahora mismo hay dos estados: pill y panel completo. Falta el intermedio, y es probablemente el que más se va a usar.

**Respuesta efímera** — tras contestar, J muestra solo su última respuesta flotando sobre el vidrio, sin cabecera pesada ni pie, y se desvanece a los ~6 s. Sin tabla, sin tool calls, sin historial.

La mayoría de interacciones con un asistente de voz son "pon un temporizador", "cuánto son 200 dólares en euros". Esas nunca deberían abrir un panel de 380×700. Este estado es el que hace que el widget sea de verdad no invasivo: **el panel completo pasa a ser la excepción, no el destino por defecto**.

Escalado de estados:

```
pill (230 px)  →  respuesta (340 px)  →  panel (380 px)
   escuchando        respondió            hay algo que explorar
```

---

## 5. Motion

Principio: **llega con calma, se va rápido**. Aparecer es un evento que merece presencia; desaparecer no debe hacerse esperar.

| Transición | Especificación |
|---|---|
| Aparición | spring 0.42 s, `cubic-bezier(.32,.72,0,1)` · opacity 0→1 en 0.18 s · scale 0.96→1 · translateY −6→0 · sin overshoot |
| Desaparición | 0.24 s ease-out, solo opacity + scale 1→0.97 |
| Cascada de contenido | stagger de 40 ms por bloque, fade + slide-up de 8 px |
| Cambio de altura | spring sobre `height`, el contenido hace crossfade — nunca salto de layout |
| Orbe en reposo | breathing 2.6 s ease-in-out, scale 1→1.07 |
| Waveform | 6 barras reactivas a amplitud real del micro, no animación decorativa |
| Tool call en curso | barra de progreso indeterminada 1.6 s; al completarse el estado hace crossfade sin mover el layout |

`prefers-reduced-motion` → todo colapsa a fades de 0.15 s.

---

## 6. Ejecución en Figma — orden optimizado para gastar pocas llamadas

Llegamos al límite del plan Starter con ~12 llamadas. El plan de mañana las agrupa para hacer todo en **7–8**.

| # | Llamada | Contenido |
|---|---|---|
| 1 | `use_figma` | Colección de variables **HUD** en una sola pasada: material (tinte, opacidad, blur), jerarquía de etiqueta (.95/.62/.48), separadores, rellenos, radios, acentos de marca |
| 2 | `use_figma` | Tres frames de contexto 1440×900 con wallpapers de prueba (degradado a color, documento claro, editor oscuro) + barra de menú |
| 3 | `use_figma` | Componente **Orbe** con variantes: `idle / listening / thinking / speaking` |
| 4 | `use_figma` | Estado pill + estado respuesta efímera, ambos completos |
| 5 | `use_figma` | Panel completo: cabecera, conversación sin burbujas, pie |
| 6 | `use_figma` | Widgets ricos: tool call, tabla, imagen |
| 7 | `get_screenshot` | Verificación visual de los tres fondos |
| 8 | reserva | Correcciones |

**Regla para mañana:** cero llamadas exploratorias. El mockup HTML es la fuente de verdad; en Figma solo transcribimos.

### Componentización

Convertir en componentes de Figma con variantes (no lo hicimos y hay que hacerlo):

- `Orbe` → 4 estados
- `HUD / Superficie` → 3 tamaños (pill, respuesta, panel)
- `Turno` → usuario / J
- `Widget / Tool call` → en curso / completado / error
- `Widget / Tabla`, `Widget / Imagen`

---

## 7. Qué queda abierto

- **Modo de solo lectura del historial** — si el panel completo debe permitir scroll a conversaciones anteriores o solo a la sesión actual.
- **Comportamiento multi-monitor y espacios** — `canJoinAllSpaces` implica decisiones de dónde reaparece.
- **Estado de error** — qué pasa cuando una tool call falla o se pierde la conexión con el gateway. No está diseñado.
- **Interacción por teclado** — hay un `⌘⇧J` de sugerencia en el mockup, sin definir.
- **Relación con `jota-display`** — este widget y la futura capa visual comparten componentes. Merece decidirse antes de componentizar demasiado.

---

## Anexo · el mockup

`jvoice-glass-mockup.html` acompaña a este documento. Ábrelo en el Mac a pantalla completa y usa los controles de abajo a la izquierda:

- **Fondo** — wallpaper / documento blanco / editor de código. El documento blanco es la prueba real del vidrio.
- **Estado** — pill / respuesta / panel.
- **Opacidad** — 60 / 72 / 84 %, para que veas por ti mismo por qué 60 no vale.
