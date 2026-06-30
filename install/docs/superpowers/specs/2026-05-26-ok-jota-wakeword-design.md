# Spec: Wake word personalizada "ok jota" + jota-wake-trainer

**Fecha:** 2026-05-26
**Estado:** aprobado por el usuario
**Autor:** Alfonso Garre + Claude

---

## Objetivo

Crear el pipeline completo para entrenar la wake word `ok jota` y desplegarla en el ecosistema jota-voice. El resultado son dos artefactos:

1. **`jota-wake-trainer`** — herramienta CLI reutilizable para entrenar cualquier wake word compatible con openWakeWord y wyoming.
2. **`ok_jota.tflite`** — el modelo resultante, versionado en `jota-wake-trainer/models/`, consumido por jota-voice.

El modelo debe:
- Funcionar para cualquier voz (no solo los 3 residentes)
- Tener rendimiento idéntico al modelo `ok_nabu` actual en el Huawei P8 Lite
- Integrarse sin cambios en wyoming-openwakeword, wyoming-satellite ni Home Assistant

No se aborda speaker identification en este spec (queda como fase futura separada).

---

## Arquitectura del modelo (rendimiento en el teléfono)

openWakeWord funciona en dos capas en tiempo de inferencia:

1. **Feature extractor (AudioSet embedding, ~30 MB)** — ya cargado en el proceso actual, compartido entre todos los modelos. No cambia con el modelo custom.
2. **Clasificador binario (`ok_jota.tflite`, ~3-8 KB)** — el único archivo que entrenamos y sustituimos.

El modelo custom no añade latencia ni peso al teléfono. Rendimiento idéntico a `ok_nabu`.

---

## Repositorio: jota-wake-trainer

Repo independiente dentro del ecosistema J (`/workspace/jota-wake-trainer`).

**Por qué independiente:** el pipeline es genérico — con cambiar el texto y la config entrena cualquier wake word. Es una herramienta reutilizable, no código de un solo uso. El ecosistema jota-voice lo referencia como fuente del modelo.

### Stack tecnológico

| Capa | Tecnología | Motivo |
|------|-----------|--------|
| Lenguaje | Python 3.11+ | Ecosistema ML estándar |
| CLI | **Typer** | Type-annotated, auto-genera `--help`, moderno |
| Terminal UI | **Rich** | Progress bars, tablas, colores |
| Config | **Pydantic** + YAML | Validación estricta de configs, errores claros |
| Audio recording | **sounddevice** | Cross-platform, captura del micro directamente |
| Audio processing | **soundfile** + **librosa** | Normalización, resampling, validación WAV |
| Síntesis | Protocolo abstracto + implementaciones por proveedor | Ver sección Síntesis |
| Entrenamiento | **openWakeWord** training API + **PyTorch** MPS | Oficial, funciona en Apple Silicon M2 |
| Linting | **Ruff** | Reemplaza flake8+isort+black |
| Type checking | **mypy** | Tipos en todo el código |
| Tests | **pytest** | Módulos de audio y síntesis (no el ML) |

### Estructura de directorios

```
jota-wake-trainer/
├── pyproject.toml
├── .gitignore
├── README.md
├── configs/
│   └── ok_jota.yaml              ← config pública de ejemplo
├── configs.local/                ← gitignoreado (API keys, URLs privadas)
│   └── ok_jota.local.yaml
├── docs/
│   ├── getting-started.md
│   ├── recording-guide.md
│   └── integracion-jota-voice.md
├── models/
│   └── ok_jota.tflite            ← artefacto final, versionado (~5 KB)
├── data/                         ← gitignoreado
│   ├── positivos/
│   │   ├── persona1/
│   │   ├── persona2/
│   │   └── persona3/
│   └── sintetizados/             ← WAVs de cualquier fuente TTS
├── trainer/
│   ├── __init__.py
│   ├── cli.py                    ← entry point Typer
│   ├── config.py                 ← modelos Pydantic
│   ├── audio/
│   │   ├── recorder.py           ← grabación interactiva
│   │   └── processor.py          ← normalización, resampling, validación
│   ├── synthesis/
│   │   ├── base.py               ← Protocol: Synthesizer
│   │   ├── piper.py              ← Piper TTS (local, default)
│   │   └── openai_compatible.py  ← cualquier servidor /v1/audio/speech + /v1/voices
│   ├── training/
│   │   ├── augmentation.py
│   │   ├── trainer.py
│   │   └── export.py
│   └── evaluation/
│       └── evaluator.py
└── scripts/                      ← helpers shell opcionales
```

---

## CLI — subcomandos

```
jota-wake record       # Grabación interactiva guiada
jota-wake synthesize   # Síntesis multi-proveedor + setup si es necesario
jota-wake train        # Entrenamiento + exportación a TFLite
jota-wake evaluate     # Prueba el modelo contra muestras de test
```

### `jota-wake record` — grabación interactiva

Guía al usuario clip a clip a través de las 10 condiciones de la tabla. Flujo por clip:

```
[ Condición 4 de 10 ] — Ruido TV/radio
  TV o radio de fondo a volumen moderado, distancia ~1.5m
  Clips grabados: 2 / 4

  Pulsa ENTER para grabar... ████████████ grabando (3s)

  ▶ Reproducir clip? [s/N]
  ¿Guardar este clip? [S/reintentar/saltar]
```

Progreso global visible en todo momento. Permite reintentar cualquier clip.

#### Tabla de condiciones de grabación

| # | Condición | Descripción | Clips/persona |
|---|-----------|-------------|:---:|
| 1 | Distancia normal · silencio | 1-1.5 m del dispositivo, habitación en silencio | 5 |
| 2 | Distancia cercana · silencio | 30-50 cm del dispositivo | 3 |
| 3 | Distancia larga · voz alzada | 3-4 m del dispositivo | 3 |
| 4 | Ruido TV/radio | TV o radio de fondo, volumen moderado | 4 |
| 5 | Ruido de conversación | Otra persona hablando en la misma habitación | 3 |
| 6 | Música de fondo | Música a volumen normal | 3 |
| 7 | Voz rápida | Dicho con prisa | 3 |
| 8 | Voz lenta | Pausado, sobrearticulado | 2 |
| 9 | Voz baja / susurro | Tono bajo, sin proyectar | 2 |
| 10 | Ángulo lateral | Hablando de lado al dispositivo (~45°) | 2 |
| | **Total por persona** | | **30** |
| | **Total 3 personas** | | **90** |

Formato de salida: WAV 16kHz mono 16-bit. 1 segundo de silencio pre/post clip.

---

### `jota-wake synthesize` — síntesis multi-proveedor

#### Proveedores soportados

| Tipo | Requisitos de compatibilidad |
|------|------------------------------|
| **Piper** (local, default) | Binario + carpeta `piper/voices/`. Auto-descubrimiento de voces por `.onnx.json` |
| **OpenAI-compatible** | DEBE implementar `POST /v1/audio/speech` + `GET /v1/voices`. Si falla `/v1/voices` → proveedor rechazado |

ElevenLabs, jota-speaker, y cualquier servidor local compatible entran en la segunda categoría.
La config de proveedores adicionales se guarda en `configs.local/` (gitignoreado).

#### Flujo de `jota-wake synthesize`

```
1. Cargar config local
   ├── Solo Piper configurado →
   │   Explicar al usuario:
   │   "Solo tienes Piper configurado. El modelo funcionará, pero con menor
   │    diversidad de voces. Puedes añadir proveedores OpenAI-compatible
   │    (ElevenLabs, servidor local, etc.) para mejorar la calidad.
   │    ¿Quieres configurar uno ahora?"
   │   ├── Sí → setup interactivo (ver abajo)
   │   └── No → continuar con Piper
   └── Hay proveedores adicionales →
       Mostrar resumen de lo configurado y continuar

2. Para cada proveedor OpenAI-compatible a añadir:
   ├── Pedir base_url + api_key
   ├── Llamar a GET /v1/voices
   │   ├── Falla → "Proveedor no compatible: requiere GET /v1/voices. Descartado."
   │   └── OK → mostrar tabla de voces disponibles
   ├── Usuario selecciona voces
   └── Mostrar preview en tiempo real:
       "5 voces seleccionadas → 9 clips/voz · 5 velocidades · 45 clips base
        Diversidad: 3F / 2M · es-ES, es-MX, en-US ✓"
       Advertencias si falta diversidad (sin bloquear)

3. Calcular distribución adaptativa (ver sección Distribución)

4. Síntesis por proveedor

5. Resumen: X clips generados de Y proveedores → data/sintetizados/
```

#### Distribución adaptativa de clips

**Target fijo:** 45 clips sintéticos base (independientemente del número de voces).

```
clips_por_voz = 45 / total_voces
velocidades    = linspace(0.7, 1.4, clips_por_voz)  ← distribuidas uniformemente

Ejemplos:
  3 voces  → 15 clips/voz → 15 velocidades entre 0.7 y 1.4
  9 voces  →  5 clips/voz → 0.8 · 0.9 · 1.0 · 1.1 · 1.2
 15 voces  →  3 clips/voz → 0.85 · 1.0 · 1.15
```

Límites:
- **< 3 voces en total** → aviso + recomendación de añadir más
- **Mínimo 2 speeds por voz**
- **Máximo 15 speeds por voz**

Validaciones de diversidad (avisos, sin bloquear):
- Todas las voces del mismo género
- Todas las voces del mismo idioma

#### Síntesis manual (siempre disponible)

El usuario puede depositar WAVs en `data/sintetizados/` desde cualquier fuente (ElevenLabs web, Audacity, etc.). El trainer los usa sin distinción de origen. Formato: WAV 16kHz mono 16-bit.

---

### `jota-wake train`

```bash
jota-wake train --config configs/ok_jota.yaml
# Duración estimada: 30-60 min en MacBook Air M2
# Salida: models/ok_jota.tflite
```

- Lee muestras de `data/positivos/` + `data/sintetizados/`
- Aplica augmentación automática: room impulse responses, ruido de fondo, variaciones de volumen (×10-15)
- Entrena con PyTorch MPS (Apple Silicon) o CUDA si disponible
- Exporta a TFLite

---

### `jota-wake evaluate`

Prueba el modelo entrenado contra un conjunto de clips de validación y reporta:
- Tasa de detección correcta
- Tasa de falsos positivos
- Threshold recomendado

---

## Despliegue en jota-voice

Una vez entrenado, copiar `models/ok_jota.tflite` al Huawei P8 Lite:

```bash
scp models/ok_jota.tflite \
  -P 8022 u0_a161@192.168.1.129:\
  /data/data/com.termux/files/home/oww-venv/lib/python3.13/\
  site-packages/wyoming_openwakeword/models/
```

Cambios en el teléfono:
- `--preload-model ok_nabu` → `--preload-model ok_jota`
- `--wake-word-name ok_nabu` → `--wake-word-name ok_jota`

Sin cambios en wyoming-satellite, Home Assistant ni ningún otro servicio.

---

## Criterios de éxito

- "ok jota" dicho por cualquier persona activa el pipeline de HA
- Falsos positivos comparables a `ok_nabu` con threshold 0.3
- Sin degradación de latencia respecto al sistema actual
- Los 3 residentes confirman detección fiable en todas las condiciones de la tabla

---

## Fuera de alcance (fase futura)

- **Speaker identification:** embeddings de voz en el pipeline STT para identificar locutor y condicionar accesos
- **Reentrenamiento iterativo:** el entorno queda disponible en jota-wake-trainer

---

## Dependencias y riesgos

| Riesgo | Mitigación |
|--------|------------|
| Proveedor sin `/v1/voices` | Rechazado con mensaje claro; usuario usa Piper o síntesis manual |
| Pocas voces → poca diversidad | Distribución adaptativa + avisos en CLI |
| Modelo con muchos falsos positivos | Subir threshold 0.3 → 0.4-0.5; añadir muestras negativas |
| Modelo que no detecta voces lejanas | Grabar muestras a distancias variadas (condición 3 de la tabla) |
| PyTorch MPS vs CUDA resultados distintos | Verificar .tflite en el teléfono antes de dar por concluido |
| Parche `handler.py` incompatible | El parche solo toca el handshake Info, no la inferencia — sin riesgo |
