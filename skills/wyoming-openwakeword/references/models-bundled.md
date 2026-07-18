# Modelos pre-entrenados en la imagen 1.10.0

## Tabla de modelos bundled

La imagen `rhasspy/wyoming-openwakeword:1.10.0` incluye estos modelos en
`/usr/src/.venv/lib/python3.11/site-packages/wyoming_openwakeword/models/`:

| Modelo (fichero) | Wake word | Tamaño | Idioma optimizado | Notas |
|---|---|---|---|---|
| `hey_jarvis_v0.1.tflite` | "hey jarvis" | 1.2 MB | inglés americano | **El más usado en jota-voice**. Buena precisión. |
| `alexa_v0.1.tflite` | "alexa" | ~1 MB | inglés americano | Alternativa popular. |
| `hey_mycroft_v0.1.tflite` | "hey mycroft" | ~1 MB | inglés americano | Alternativa. |
| `hey_rhasspy_v0.1.tflite` | "hey rhasspy" | ~1 MB | inglés americano | Creador de Wyoming. |
| `okay_nabu_v0.1.tflite` | "okay, nabu" | ~200 KB | inglés americano | Modelo "tiny" legacy. |
| `hey_snowboy_v0.1.tflite` | "hey snowboy" | ~1 MB | inglés americano | De KITT.AI (Snowboy). |
| `current weather_v0.1.tflite` | "current weather" | ~1 MB | inglés americano | **Frase, no wake word simple**. Requiere `trigger_level` más alto. |
| `timers_v0.1.tflite` | "timer" | ~1 MB | inglés americano | Frase corta. |

**Importante**: los nombres internos que Wyoming espera son los **stems
de los ficheros** (sin `.tflite`). El cliente manda
`detect` con `{"names": ["hey_jarvis"]}` y Wyoming busca
`hey_jarvis_v0.1.tflite` y carga el modelo con key `hey_jarvis_v0.1`.

## Licencia

- **Código**: Apache-2.0 (libre uso comercial).
- **Modelos pre-entrenados**: **CC BY-NC-SA 4.0** (no comercial, compartir
  igual). Esto significa que si jota-voice se hace comercial algún día, los
  modelos bundled NO se pueden usar; habría que re-entrenar modelos propios
  con datos limpios de licencia.

## Rendimiento reportado

Por `dscripka/openWakeWord` (con threshold bien ajustado):
- <5% false-reject (wake word dicha pero no detectada).
- <0.5/hora false-accept (detección sin wake word dicha).

Estos números son con audio limpio de micro de buena calidad y modelos
pre-entrenados para el idioma correcto. En la práctica, con audio
ambiental y voces no nativas, los números empeoran.

## Limitación de idioma

Los modelos pre-entrenados están **optimizados para inglés americano**.
Con español o acentos distintos, los scores bajan drásticamente. En
nuestras pruebas (voces en español, Razer Seiren Mini, threshold 0.15):
- `hey_jarvis`: scores típicos 0.05-0.45, con picos > 0.15 cada 2-3 intentos.
- `ok_jota` (custom sub-entrenado): scores < 0.05 siempre.

**Conclusión**: si tu voz no es inglés americano, planifica re-entrenar
un modelo custom. Ver `custom-model-training.md`.

## Cómo probar modelos sin instalar nada

[davidscripka/openWakeWord](https://huggingface.co/spaces/davidscripka/openWakeWord)
es un demo online que carga el último modelo y acepta audio del navegador.
Útil para:
- Verificar que un modelo concreto detecta tu voz **antes** de instalar.
- Comparar `hey_jarvis` vs `alexa` vs `hey_mycroft` y elegir el que mejor
  te funcione.
- Probar frases custom que ya estén en el demo (no permite re-entrenar
  desde el demo).
