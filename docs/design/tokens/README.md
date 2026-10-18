# Design tokens · Jota

> **Estado: exploración.** Los valores están razonados y verificados por contraste, pero el sistema no está cerrado.

Tokens en formato **W3C DTCG**, el estándar que Penpot consume de forma nativa. El mismo archivo sirve para Penpot, para Style Dictionary, o para generar CSS directamente — no es un formato de Penpot, es un estándar.

## Reglas de nombrado — leer antes de tocar los JSON

Penpot rechaza la importación entera si un nombre no cumple:

> Los nombres de token solo pueden contener **letras y dígitos separados por puntos**, y no pueden empezar por `$`.

Eso significa **sin barras y sin guiones**, tanto en los nombres de set como en los de token. Nos costó una importación fallida: `global/color` y `orb-start` eran inválidos. Ahora son `global` y `orbStart`.

La agrupación en carpetas con `/` que menciona la documentación funciona **renombrando sets desde la UI**, no en el JSON de importación.

Antes de importar nada, valida:

```bash
python3 validar.py
```

Comprueba nombres de set, nombres de token y que todos los alias resuelvan. Devuelve exit 1 si algo falla.

## Los dos archivos y por qué son dos

| Archivo | Va en | Set | Contiene |
|---|---|---|---|
| `dusk-core.tokens.json` | Archivo **`Dusk-core`**, publicado como Shared Library | `global` | La marca: paleta, escala tipográfica, espaciado, radios |
| `j-voice-mac.tokens.json` | Archivo **`j-voice-mac`** | `hud` | Solo lo específico del HUD de macOS: superficies translúcidas, material, medidas del widget |

**Por qué separados.** `jota-web`, `jota-landing` y `jota-display` van a necesitar la paleta Malva/Sky/Linen y la tipografía, pero no tienen ningún uso para `surface.whisper` ni para `hud/material.blur`. Si metes todo junto, cada superficie hereda tokens de un material de macOS que no le sirve. Y al revés: si la marca vive dentro de `j-voice-mac`, el día que arranques la web tendrás que extraerla y reconectar todo.

Los tokens del HUD **referencian** a los globales (`{global/color.malva.400}`), así que cambiar el Malva de marca se propaga solo.

Si prefieres empezar simple, se colapsa fácil: importa los dos archivos en `j-voice-mac` y ya. Separarlo después cuesta más que hacerlo ahora, pero tampoco es dramático.

## Cómo importarlos

1. Abre el archivo en Penpot → pestaña **Tokens**
2. **Tools → Import → Single JSON file**
3. Selecciona el `.json`

La clave de primer nivel (`global` o `hud`) se convierte en el nombre del **set**. Todo lo de dentro son grupos de tokens.

### Después de importar: crear el theme

Los themes se configuran en la UI, no vienen en el JSON. En **Themes → Create one**:

- Grupo: `Mode` · Theme: `Dark` · sets activos: todos
- (Más adelante, para web: grupo `Mode` · theme `Light`)

**El orden de los sets importa** — cascadea como CSS. Si un token se repite, gana el que esté más abajo. Deja `global` arriba y `hud` debajo.

## Decisiones que hay dentro

**Oscuro como principal.** El HUD no sigue el tema del sistema; es dark siempre, como Spotlight o el HUD de volumen. El set `hud/semantic` asume fondo oscuro.

**El alpha va dentro del color.** `rgba(30, 27, 38, 0.72)`, no un color sólido más una opacidad aparte. Penpot soporta RGBA y hex8 nativamente en los color tokens — esto es más limpio que en Figma, donde enlazar una variable de color ignoraba la opacidad del relleno y nos costó una tarde entera de bugs.

**Malva 400, no 500, sobre oscuro.** El `#7C6AAE` de marca se apaga sobre el vidrio; el 400 (`#9A87CE`) mantiene la identidad con presencia. Es la misma marca, calibrada para el fondo.

**Fraunces está marcada como solo-web.** Dentro del HUD es tipografía foránea. La marca vive en el orbe, no en la tipografía.

**El 0.72 no es estético.** Sale del cálculo de contraste en `../hud-widget/referencia/`. Sobre documento blanco da 6.2:1; a 0.60 cae a 4.2:1, por debajo de AA. Los tokens `surface-min` / `surface-max` de `hud/material` existen para la opacidad adaptativa que aún está por decidir.

## Lo que no está aquí

**El easing.** `cubic-bezier(0.32, 0.72, 0, 1)`, 420 ms de entrada y 240 ms de salida. El tipo `cubicBezier` no está entre los que soporta Penpot y podría hacer fallar la importación entera, así que las duraciones sí están como `number` en `hud/motion` pero la curva no. Está en `../hud-widget/PLAN.md` §5 y funcionando en la maqueta.

**El blur de fondo.** Está como token informativo, pero la Plugin API de Penpot solo expone `layer-blur`. El *backdrop blur* hay que aplicarlo desde la UI — pendiente de verificar hasta dónde llega en la interfaz.

## Si la importación falla

Ya cayó una vez por nombres con barra y guion — corregido y cubierto por `validar.py`.

Lo que aún no está verificado son los `$type`. Uso los del estándar DTCG y los que documenta Penpot, pero solo la importación real lo confirma. Si alguno se rechaza, el error señala el token concreto: cámbialo a `dimension` o `number`, que son los más permisivos, y avísame para corregirlo en origen.

Tipos en uso: `color`, `dimension`, `opacity`, `number`, `sizing`, `spacing`, `borderRadius`, `strokeWidth`, `fontFamily`, `fontSize`, `fontWeight`, `letterSpacing`.
