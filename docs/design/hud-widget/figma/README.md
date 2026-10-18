# J Voice — HUD Builder

Plugin local de Figma que construye el widget completo. **No consume cuota del MCP** porque la Plugin API se ejecuta desde tu Mac.

## Cómo ejecutarlo

1. Abre el archivo `Jota-voice-mac-UI` en **Figma Desktop**
2. Menú **Plugins → Development → Import plugin from manifest…**
3. Selecciona el `manifest.json` de esta carpeta
4. **Plugins → Development → J Voice — HUD Builder**

Tarda unos segundos y se cierra solo con un resumen. Es re-ejecutable: reutiliza las variables y estilos que ya existan y rehace las páginas `JV · Components` y `JV · States` en cada pasada.

### Si falla

El mensaje de cierre dice exactamente en qué paso murió y qué se completó antes:

```
FALLÓ en "componente Tool Call" → <mensaje>  ·  completado: cargar tipografías, crear tokens, …
```

Pásame ese mensaje entero. Los avisos (`N aviso(s): …`) son degradaciones no fatales — el build terminó, pero algo prescindible no se aplicó.

## Tests

`test/` contiene un mock de la Plugin API que ejecuta `code.js` de verdad y valida invariantes reales: que `layoutSizing='FILL'` solo se use con padre auto-layout, que no se creen variables duplicadas, que no se borre la página activa ni la última, que `resize()` reciba números.

```bash
cd test && node run.js
```

Ejecuta tres pasadas seguidas para verificar idempotencia. **Correr esto antes de tocar Figma** — es gratis y detecta la mayoría de fallos.

El mock es una aproximación, no Figma. Valida estructura y ordenación de llamadas; no valida cómo se ve el resultado.

## Qué construye

**Tokens** — tres colecciones con la arquitectura estándar de design system:

| Colección | Contenido |
|---|---|
| `Primitives` | 5 colores crudos, scope vacío (ocultos de los pickers) |
| `HUD` | 10 semánticos, todos **aliased** a primitivas, un solo modo `Dark` |
| `Layout` | 5 espaciados + 4 radios |

Todas las variables llevan **scopes explícitos** (nunca `ALL_SCOPES`) y **code syntax** en formato `var(--jv-*)`, que corresponde 1:1 con el mockup HTML. Dev Mode devolverá los nombres reales de las variables CSS.

**Estilos de efecto** — `HUD/Glass` (background blur 50 + luz superior + doble sombra) y `HUD/Elevation`.

**Componentes con variantes:**

- `Orb` — `State = Idle / Listening / Thinking / Speaking`
- `Turn` — `Speaker = User / J`, con propiedad de texto editable por instancia
- `Widget / Tool Call` — `State = Running / Done / Error`
- `Widget / Table`, `Widget / Image`

**Conceptos** en la página `JV · Conceptos` — cuatro exploraciones del estado de escucha, de más etéreo a más definido:

| | Concepto | Idea | Contraste sobre blanco |
|---|---|---|---|
| A | **Aliento** | Sin contenedor. Solo un halo difuminado detrás del texto | 4.5:1 en el centro del halo, 1.0:1 fuera |
| B | **Bruma** | Velo al 30 %, blur alto, sin stroke | 1.9:1 ✗ |
| C | **Cristal** | Vidrio al 50 %, borde especular solo arriba | 3.1:1 ~ |
| D | **Estela** | Transcripción en vivo, las líneas viejas se apagan y encogen | combinable con los otros tres |

**La tensión de fondo:** cuanto más etéreo, menos legible. Sobre wallpaper oscuro los cuatro pasan de sobra; sobre un documento blanco solo aguanta el vidrio al 72 %. Y el fondo claro es justo donde el usuario trabaja.

La salida probable es **opacidad adaptativa** — muestrear la luminancia de lo que hay detrás y mover la superficie entre 30 % y 72 %. macOS ya hace algo así con sus materiales. Está anotado en la propia página.

**Prototipo interactivo** en la página `JV · Prototype` — tres pantallas encadenadas. Dale a ▶ arriba a la derecha y haz clic para recorrer `escuchando → respuesta → panel → escuchando`.

El HUD se llama igual (`HUD`) en las tres pantallas, así que **Smart Animate lo interpola** en vez de cortar: se ve el ensanchamiento y el crecimiento reales. El easing es el del plan, `cubic-bezier(.32,.72,0,1)`; ir hacia delante dura 0.42 s y volver 0.24 s — llega con calma, se va rápido.

**Estados**, cada uno sobre un fondo distinto para juzgar el vidrio en condiciones reales:

| Estado | Fondo | Por qué |
|---|---|---|
| Panel (380) | Wallpaper | El caso bonito |
| Respuesta efímera (340) | Documento blanco | **El caso duro** — aquí es donde el vidrio se rompe si está mal calibrado |
| Pill (230) | Editor oscuro | El caso fácil |

Los tres van anclados arriba a la derecha, `y=38` (bajo la barra de menú), `x` a 14 px del borde.

## Notas

**Tipografía** — usa SF Pro Text y SF Mono si las tienes instaladas; si no, cae a Inter y Roboto Mono automáticamente. Para que se vea como en el mockup, instala [SF Pro](https://developer.apple.com/fonts/) (viene con los UI kits de Apple que ya tienes vinculados al archivo).

**Blur** — Figma renderiza `BACKGROUND_BLUR` solo sobre contenido que esté *detrás dentro del mismo frame*. Por eso los estados se insertan como hijos de los frames de contexto: si los sacas fuera, el vidrio se ve plano.

**Opacidades** — el `0.72` de la superficie no es estético, sale del cálculo de contraste. A `0.60` el texto primario cae a 4.2:1 sobre blanco, por debajo de AA. Si tocas ese valor, recalcula.

## Trampas de la Plugin API que ya nos han mordido

Están todas cubiertas por el test, pero conviene saberlas antes de tocar `code.js`:

**`resize()` resetea los modos de sizing a `FIXED`.** Si llamas a `resize()` después de poner `primaryAxisSizingMode = 'AUTO'`, pierdes el hug y el contenedor se queda clavado a la altura del placeholder — el contenido se desborda y todo se solapa. Por eso existe el helper `sizing()`: resize primero, modos después. No invertir ese orden.

**`x`/`y` antes de `appendChild` se reinterpretan.** Las coordenadas pasan de absolutas a relativas al insertar el nodo. De ahí el helper `place()`.

**Las variables sobreviven al borrado de páginas.** Viven en el documento. Borrar la página y reejecutar da `duplicate variable name` si el script no comprueba antes de crear.

**Una propiedad de componente TEXT sobrescribe el contenido de todas las variantes** con su valor por defecto. Se intentó en `Turn` y dejaba "Texto del turno" en ambos turnos; está retirada.

**Enlazar una variable de color a un paint ignora `paint.opacity`.** Figma usa el RGBA completo de la variable. Si la variable tiene alpha 1, todo se renderiza opaco por mucho que pongas `opacity: 0.055`. Esto hacía que el vidrio saliera sólido, la tarjeta de tool call saliera blanca y los bordes salieran duros — los tres síntomas eran la misma causa. **El alpha va dentro de la variable**, por eso las semánticas translúcidas no son alias: un alias arrastraría alpha 1.

## Lo que el plugin NO hace

Animación por keyframes dentro de un mismo frame. La API de motion (`applyManualKeyframeTrack`, `animationStyles`) está tras el feature flag `metronome` y no está en todas las cuentas. El prototipo con Smart Animate cubre las transiciones entre estados; lo que falta es el detalle en bucle — respiración del orbe, barras del waveform, shimmer de la barra de progreso. Eso está solo en el mockup HTML.
