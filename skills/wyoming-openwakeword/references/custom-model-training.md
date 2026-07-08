# Entrenar un modelo custom con openWakeWord

## Cuándo NO re-entrenar

Antes de invertir horas, comprueba que el problema NO es otro:

- **Threshold alto**: con 0.3, muchos scores legítimos no llegan. Baja a
  0.1 primero.
- **Modelo incompatible con tu voz**: prueba varios modelos bundled
  (`hey_jarvis`, `alexa`, `hey_mycroft`) en el demo online
  [davidscripka/openWakeWord](https://huggingface.co/spaces/davidscripka/openWakeWord)
  antes de entrenar uno.
- **Micrófono de baja calidad**: scores < 0.05 constantes suelen ser
  problema de captura, no de modelo.

## Cuándo SÍ re-entrenar

- **Hablas español** u otro idioma no inglés: los modelos pre-entrenados
  no funcionan bien.
- **Quieres una frase propia** que no es "hey X" (e.g. "oye jota",
  "despierta").
- **Voz muy distinta a la media**: niños, personas mayores con voz
  temblorosa, voces con acento regional fuerte.

## Pipeline de entrenamiento

Todo el pipeline está en el repo `dscripka/openWakeWord`. Dos opciones:

### Opción A: Google Colab (rápido, sin experiencia)

`notebooks/automatic_model_training.ipynb` en el repo. <1 hora de trabajo,
interfaz guiada. Genera datos sintéticos automáticamente con TTS.

Limitaciones:
- No controlas los parámetros de entrenamiento.
- Tienes que subir los datos positivos a Google Drive.

### Opción B: Notebook detallado (más control)

`notebooks/automatic_model_training.ipynb` ejecutado local con más
iteraciones. Permite ajustar:
- Número de muestras positivas (recomendado 2-3 mil).
- Datos negativos (negatives) — el repo incluye datasets de habla, ruido y
  música (~30 000 horas).
- Hiperparámetros de la red.

## Requisitos de datos

- **Positivas**: 2-3 mil muestras de la wake word dicha en distintos tonos,
  velocidades, con ruido de fondo. Generador sintético:
  `dscripka/synthetic_speech_dataset_generation` (usa TTS para crear
  variaciones).
- **Negativas**: ~30 000 horas de habla general, ruido, música, otros
  podcasts. El repo de openWakeWord tiene datasets pre-empaquetados.

## Tamaño esperado del modelo

- Modelo bien entrenado: **1-4 MB** (similar a `hey_jarvis` que pesa 1.2 MB).
- Modelo sub-entrenado: <500 KB, no detecta con voz real.

`ok_jota` actual: **207 KB** → claramente sub-entrenado. Hay que re-entrenarlo
desde cero con más datos y/o más iteraciones.

## Output

Un fichero `.tflite` que se mete en `--custom-model-dir` (volumen
`$HOME/wyoming-data/` en Mac, o `~/oww-data/` en Termux). El nombre del
fichero debe seguir el formato `<palabra>_v<version>.tflite` (e.g.
`ok_jota_v0.2.tflite`), pero por el bug regex de Wyoming 1.10.0, **mejor
usar el nombre sin sufijo `_v<n>`** hasta que se arregle upstream.

## Convención de nombres en jota-voice

En `devices/<id>/config.yaml`:
```yaml
oww:
  wake_words:
    - "ok_jota"      # nombre que el cliente pide en `detect`
```

Wyoming busca `ok_jota*.tflite` en `--custom-model-dir`. Si el fichero se
llama `ok_jota.tflite` (sin `_v<n>`), lo encuentra y carga. Si se llama
`ok_jota_v0.2.tflite`, el bug regex de Wyoming 1.10.0 lo trunca a `ok`.
