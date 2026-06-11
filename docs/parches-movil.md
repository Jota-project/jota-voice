# Parches aplicados al Huawei P8 Lite

Registro de modificaciones manuales sobre librerías de terceros en el teléfono.
Estos parches **se perderán si se actualiza wyoming-satellite, wyoming-openwakeword o se
reinstala el entorno**. Al detectar roturas, consultar este documento primero.

---

## 1. wyoming-satellite — pre-roll de audio

**Archivo:** `~/wyoming-satellite/wyoming_satellite/satellite.py`
**Backup:** `~/wyoming-satellite/wyoming_satellite/satellite.py.bak`
**Fecha:** 2026-05-20
**Motivo:** `WakeStreamingSatellite` (usado con `--wake-uri`) no tiene pre-roll nativo.
Sin este parche, el audio pronunciado durante/justo después de la wake word se pierde
porque el satélite no lo enviaba al STT.

### Cambios (3 sitios):

**1. `__init__` de `WakeStreamingSatellite`** — añadir tras `self._wake_info_ready`:
```python
# Pre-roll: ring buffer con los últimos 1.5s de audio durante la detección
# de wake word. Se envía al STT justo después de la detección, antes del
# audio nuevo, para que el modelo reciba la palabra entera + lo que se
# dijo al instante.
_preroll_bytes = int(1.5 * 16000 * 2)  # 1.5s a 16kHz 16-bit mono
self._preroll_buffer: RingBuffer = RingBuffer(maxlen=_preroll_bytes)
self._preroll_rate: int = 16000
self._preroll_width: int = 2
self._preroll_channels: int = 1
```

**2. `event_from_mic`** — en el `else` (cuando no está streaming, reenvía a wake service):
```python
else:
    # Forward to wake word service
    await self.event_to_wake(event)
    # Acumular audio en el buffer de pre-roll
    if audio_bytes is None:
        chunk = AudioChunk.from_event(event)
        audio_bytes = chunk.audio
        self._preroll_rate = chunk.rate
        self._preroll_width = chunk.width
        self._preroll_channels = chunk.channels
    self._preroll_buffer.put(audio_bytes)
```

**3. `event_from_wake`** — tras `await self._send_run_pipeline(...)`:
```python
# Enviar pre-roll: audio capturado durante la detección de wake word
preroll_audio = self._preroll_buffer.getvalue()
if preroll_audio:
    await self.event_to_server(
        AudioChunk(
            rate=self._preroll_rate,
            width=self._preroll_width,
            channels=self._preroll_channels,
            audio=preroll_audio,
        ).event()
    )
# Limpiar el buffer para el siguiente ciclo
self._preroll_buffer.put(bytes(self._preroll_buffer.maxlen))
```

---

## 2. wyoming-openwakeword — WakeModel con campos requeridos

**Archivo:** `~/oww-venv/lib/python3.13/site-packages/wyoming_openwakeword/handler.py`
**Fecha:** 2026-05-20 (actualizado 2026-05-21)
**Motivo:** `wyoming-openwakeword 1.8.2` (PyPI) construye `WakeModel` y `WakeProgram` sin
pasar `phrase`/`version`. A partir de `wyoming >= 1.5.4` estos campos son requeridos en los
dataclasses (no tienen `= None` como default). Causa `TypeError` al hacer el handshake Info
con el satélite, que cierra la conexión inmediatamente.

El Docker oficial `rhasspy/wyoming-openwakeword` sí los pasa — de ahí la diferencia.

### Cambios:

En el método `_get_info()`, tanto en `WakeProgram` como en `WakeModel`:
```python
# ANTES (roto con wyoming >= 1.5.4):
WakeModel(
    name=model_path.stem,
    description=_get_description(model_path.stem),
    attribution=Attribution(...),
    installed=True,
    languages=[],
)

# DESPUÉS (correcto) — WakeProgram:
WakeProgram(
    name="openwakeword",
    description="...",
    version="",                                  # campo requerido
    attribution=Attribution(...),
    installed=True,
    models=[...],
)

# DESPUÉS (correcto) — WakeModel:
WakeModel(
    name=model_path.stem,
    description=_get_description(model_path.stem),
    phrase=_get_description(model_path.stem),   # campo requerido
    version="",                                  # campo requerido
    attribution=Attribution(...),
    installed=True,
    languages=[],
)
```

---

## 3. oww-venv — entorno virtual con system-site-packages

**Ruta:** `~/oww-venv/`
**Fecha:** 2026-05-20
**Motivo:** Instalar `numpy` o `tflite-runtime` via pip en un venv limpio sobre ARM
(Cortex-A53) requiere compilar desde source — tarda horas y puede no terminar.
El sistema Termux ya tiene ambos preinstalados como paquetes binarios.

**Cómo recrearlo si se pierde:**
```bash
# El sistema ya tiene numpy y tflite-runtime — heredarlos con --system-site-packages
python3 -m venv ~/oww-venv --system-site-packages

# wyoming-openwakeword: instalar sin deps (tflite-runtime-nightly no existe para ARM)
~/oww-venv/bin/pip install --no-deps wyoming-openwakeword

# wyoming: instalar versión compatible (1.9.0 rompe WakeModel, bajar a 1.5.4)
~/oww-venv/bin/pip install 'wyoming==1.5.4'

# Aplicar el parche del handler.py (ver sección 2 de este documento)
```

**Paquetes resultantes relevantes:**
- `wyoming-openwakeword 1.8.2`
- `wyoming 1.5.4`
- `numpy 2.4.4` (del sistema)
- `tflite-runtime 2.20.0` (del sistema)

---

## 4. start-satellite.sh — configuración del satélite

**Archivo:** `~/start-satellite.sh` en el teléfono
**Fecha:** 2026-05-20

Cambios respecto al script original:
- Añadido `--wake-uri tcp://localhost:10401` — wake word detection local (oww en el propio teléfono)
- Añadido `--wake-word-name ok_nabu`
- Eliminado `--awake-wav ./sounds/awake.wav` — sin pitido de activación (el pre-roll hace innecesaria la pausa post-beep)
- Mantenido `--done-wav ./sounds/done.wav`

**Script actual:**
```sh
#!/data/data/com.termux/files/usr/bin/sh
cd /data/data/com.termux/files/home/wyoming-satellite
exec .venv/bin/python3 -m wyoming_satellite \
  --name "Huawei P8 Lite" \
  --uri "tcp://0.0.0.0:10700" \
  --mic-command "rec -r 16000 -c 1 -b 16 -e signed-integer -t raw --no-show-progress -" \
  --snd-command "play -r 22050 -c 1 -b 16 -e signed-integer -t raw --no-show-progress -" \
  --wake-uri tcp://localhost:10401 \
  --wake-word-name ok_nabu \
  --done-wav ./sounds/done.wav \
  --debug >> /data/data/com.termux/files/home/wyoming-satellite.log 2>&1
```

**Para arrancar openwakeword local** (debe iniciarse antes que el satélite):
```bash
nohup ~/oww-venv/bin/python3 -m wyoming_openwakeword \
  --uri tcp://0.0.0.0:10401 \
  --preload-model ok_nabu \
  --threshold 0.3 \
  > ~/oww.log 2>&1 &
```

---

## 5. openwakeword en worker-01 — threshold 0.3

**Servicio:** Docker `wyoming-openwakeword` en worker-01
**Fecha:** 2026-05-20
**Estado actual:** parado (la detección la hace el teléfono)

Si se reactiva, el contenedor debe incluir `--threshold 0.3`:
```bash
docker run -d \
  --name wyoming-openwakeword \
  --restart unless-stopped \
  -p 10400:10400 \
  rhasspy/wyoming-openwakeword \
  --uri tcp://0.0.0.0:10400 \
  --preload-model ok_nabu \
  --threshold 0.3
```
