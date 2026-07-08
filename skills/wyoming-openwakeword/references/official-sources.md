# Fuentes oficiales verificadas

**Fecha de verificación**: 2026-07-08. Si algún enlace queda roto, abrir
issue en el repo `jota-voice`.

## Repos oficiales

- [**rhasspy/wyoming-openwakeword**](https://github.com/rhasspy/wyoming-openwakeword) — Apache-2.0. Repo del servidor Wyoming OWW. Última release `v2.1.0` (2025-10-28, rota). Última funcional `1.10.0` (2024-02-18). 200 stars.
- [**dscripka/openWakeWord**](https://github.com/dscripka/openWakeWord) — Apache-2.0 (código) + CC BY-NC-SA 4.0 (modelos pre-entrenados, **no comercial**). Repo de la librería openWakeWord. Última release `v0.6.0` (2024-02-11). 2.5k stars. Incluye `notebooks/automatic_model_training.ipynb` y datasets de habla/ruido/música.
- [**rhasspy/wyoming**](https://github.com/rhasspy/wyoming) — Apache-2.0. Repo del protocolo Wyoming. Última release `v1.10.0` (2026-07-02). Documenta los eventos `detect`, `audio-start`, `audio-chunk`, `detection`, `not-detected`, etc.

## Docker Hub

- [**hub.docker.com/r/rhasspy/wyoming-openwakeword/tags**](https://hub.docker.com/r/rhasspy/wyoming-openwakeword/tags) — Tags de imagen Docker. `1.10.0` es la última con `linux/arm/v7` (importante para Raspberry Pi vieja, Huawei P8 Lite, etc.). 14 estrellas, 1M+ descargas.

## Issues upstream relevantes

- [**rhasspy/wyoming-openwakeword#53**](https://github.com/rhasspy/wyoming-openwakeword/issues/53) — Imagen 2.1.0 rota, `IndexError` silencioso. **ABIERTO, sin PRs, sin branches de desarrollo**. Severidad "Crítico bloqueante" reportada por el usuario.

## Demos y herramientas online

- [**HuggingFace Spaces `davidscripka/openWakeWord`**](https://huggingface.co/spaces/davidscripka/openWakeWord) — Demo interactivo en navegador, carga el último modelo y acepta audio del micro. Útil para probar modelos sin instalar nada. No permite re-entrenar.

## Implementaciones no oficiales (referencia, no usadas en jota-voice)

- [**dalehumby/openWakeWord-rhasspy**](https://github.com/dalehumby/openWakeWord-rhasspy) — Docker wrapper alternativo. No es la imagen oficial.
- [**rhasspy/openWakeWord-cpp**](https://github.com/rhasspy/openWakeWord-cpp) — Binding C++ de openWakeWord. Útil para clientes embebidos (Raspberry Pi sin Python).

## Generadores de datos sintéticos

- [**dscripka/synthetic_speech_dataset_generation**](https://github.com/dscripka/synthetic_speech_dataset_generation) — Genera muestras positivas de wake words usando TTS. Usado en el notebook de entrenamiento automático.

## Paper / preprint

**No hay paper formal de openWakeWord.** La librería no tiene publicación
académica propia. La referencia más cercana es el paper de Fluent Speech
Commands ([arxiv.org/abs/1910.09463](https://arxiv.org/abs/1910.09463)),
que describe el dataset de embeddings usado para entrenar los modelos de
openWakeWord, pero **no describe openWakeWord en sí**.

Si necesitas citar openWakeWord en un paper o presentación, cita el repo:
```
@software{scripka_openwakeword_2024,
  author = {Scripka, David},
  title = {openWakeWord},
  year = {2024},
  url = {https://github.com/dscripka/openWakeWord}
}
```

## Licencias — resumen

| Componente | Licencia | Uso comercial |
|---|---|---|
| Código openWakeWord | Apache-2.0 | ✅ Sí |
| Código wyoming-openwakeword | Apache-2.0 | ✅ Sí |
| Código protocolo Wyoming | Apache-2.0 | ✅ Sí |
| Modelos pre-entrenados | **CC BY-NC-SA 4.0** | ❌ **No** |
| Datasets de habla/ruido | varía | comprobar cada uno |
