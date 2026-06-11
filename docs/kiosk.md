# Kiosk de voz — Huawei P8 Lite

Interfaz visual para el satélite Wyoming. El teléfono actúa como terminal de voz dedicado:
pantalla apagada en reposo, se enciende al detectar la wake word, muestra transcripción
y respuesta, se apaga sola tras unos segundos.

---

## Arquitectura

```
Teléfono (192.168.1.129)
  wyoming-satellite → hook scripts → POST http://192.168.1.109:8766/state
                                          ↓
                               worker-01: kiosk-server.py
                                 • actualiza estado SSE
                                 • adb shell KEYCODE_WAKEUP / KEYCODE_SLEEP

  Chrome → https://192.168.1.109:8443/
               ← GET /events (SSE stream)  ← animaciones en tiempo real
```

---

## Servicio en worker-01

**Archivo:** `/home/sito/kiosk_server.py`
**Archivos estáticos:** `/home/sito/index.html`, `/home/sito/manifest.json`
**Servicio systemd:** `~/.config/systemd/user/kiosk-server.service`

```ini
[Unit]
Description=Jota Voice Kiosk Server
After=network.target

[Service]
ExecStartPre=/usr/bin/adb connect 192.168.1.129:32906
ExecStart=/usr/bin/python3 /home/sito/kiosk_server.py
Restart=always
RestartSec=5
Environment=HOME=/home/sito

[Install]
WantedBy=default.target
```

**Puertos:**
- `:8766` HTTP — para los hook scripts del teléfono (POST /state)
- `:8443` HTTPS — para el browser (Chrome necesita HTTPS para PWA standalone)

**Certificado SSL autofirmado** en `/home/sito/kiosk_cert.pem` y `kiosk_key.pem`
(generado con SAN para IP, caduca en 10 años).

**Comandos útiles:**
```bash
systemctl --user status kiosk-server
systemctl --user restart kiosk-server
systemctl --user stop kiosk-server
journalctl --user -u kiosk-server -f
```

---

## ADB WiFi desde worker-01

El teléfono tiene **Depuración inalámbrica** activa. Worker-01 usa las mismas claves ADB
del Mac (copiadas en `~/.android/`), así que conecta sin re-emparejar.

**Puerto de conexión ADB:** `192.168.1.129:32906`
(puerto TLS del wireless debugging; estable mientras esté activo el ajuste en el teléfono)

**Reconectar si se pierde:**
```bash
adb connect 192.168.1.129:32906
adb -s 192.168.1.129:32906 shell input keyevent KEYCODE_WAKEUP
```

El servicio `kiosk-server` hace `ExecStartPre=adb connect` automáticamente al arrancar.

**Si el puerto 32906 cambia** (puede cambiar al reiniciar el teléfono):
```bash
# Ver puerto actual desde el Mac (si ADB del Mac sigue conectado):
adb -t 1 shell ss -tlnp | grep -v 127

# O mirar en el teléfono: Ajustes → Opciones de desarrollador → Depuración inalámbrica
# El puerto que aparece en la pantalla principal (no el de emparejamiento) es el de conexión.
# Actualizar en kiosk_server.py: PHONE_ADB_PORT = NUEVO_PUERTO
```

---

## Hook scripts en el teléfono

**Ubicación:** `~/kiosk/hooks/` en Termux

| Script | Disparado por | Acción |
|--------|--------------|--------|
| `on_detection.sh` | Wake word detectada | POST `{"state":"listening"}` |
| `on_transcript.sh "$1"` | STT completo | POST `{"state":"thinking","text":"..."}` |
| `on_synthesize.sh "$1"` | Respuesta lista para TTS | POST `{"state":"response","text":"..."}` |

Todos hacen POST a `http://192.168.1.109:8766/state` en background (`&`).

El servidor al recibir `listening` → `adb KEYCODE_WAKEUP` (enciende pantalla).
Al recibir `response` → programa `KEYCODE_SLEEP` con 8 segundos de delay (`AUTO_SLEEP_SECONDS`).

**Redesplegar hooks desde el Mac:**
```bash
cd /Users/alfonsogarre/Workspace/jota-voice/kiosk
bash deploy.sh
```

---

## Configuración de wyoming-satellite

`~/start-satellite.sh` en el teléfono incluye los hooks:
```sh
--detection-command "/data/data/com.termux/files/home/kiosk/hooks/on_detection.sh"
--transcript-command "/data/data/com.termux/files/home/kiosk/hooks/on_transcript.sh"
--synthesize-command "/data/data/com.termux/files/home/kiosk/hooks/on_synthesize.sh"
```

---

## UI web

**Tecnología:** HTML/CSS puro con Server-Sent Events (sin dependencias JS).

**Estados:**
| Estado | Orb | Texto | Pantalla |
|--------|-----|-------|---------|
| `idle` | Oscuro, quieto | — | Apagada |
| `listening` | Verde, barras de sonido | — | Encendida |
| `thinking` | Azul, pulsante | Puntos animados | Encendida |
| `response` | Violeta | Texto de respuesta | Encendida → apaga en 8s |

**Layout:** Grid CSS que se adapta a horizontal (orb izquierda, texto derecha) y vertical.

**PWA:** `manifest.json` con `display: standalone`. Requiere:
1. Navegar a `https://192.168.1.109:8443/` en Chrome
2. Aceptar certificado autofirmado (Avanzado → Continuar)
3. Chrome ⋮ → "Añadir a pantalla de inicio"
4. Abrir desde el icono → sin barra de navegación

---

## DNS local (dnsmasq en worker-01)

Se instaló `dnsmasq` para que el teléfono resuelva `worker-01` por nombre.
Android Chrome no soporta `.local` (mDNS), así que el teléfono necesita un DNS real.

**Config:** `/etc/dnsmasq.d/local.conf`
```
no-resolv
no-hosts
server=8.8.8.8
server=8.8.4.4
address=/worker-01/192.168.1.109
listen-address=192.168.1.109
bind-interfaces
```

**IMPORTANTE:** `DNSStubListener=yes` en `/etc/systemd/resolved.conf` debe estar activo
(no `=no`). Si se desactiva, Docker pierde DNS y el TTS de HA deja de funcionar.

Para que el teléfono use este DNS: WiFi → red → Avanzado → DNS 1 → `192.168.1.109`.
(No funcionó en la prueba inicial; por ahora el teléfono usa la IP directa.)

---

## Secuencia de arranque del teléfono

Si se reinicia Termux o el teléfono, hay que arrancar en este orden:

```bash
# 1. Openwakeword (esperar ~15s a que cargue el modelo TFLite)
nohup ~/oww-venv/bin/python3 -m wyoming_openwakeword \
  --uri tcp://0.0.0.0:10401 --preload-model ok_nabu --threshold 0.3 \
  > ~/oww.log 2>&1 &

# Esperar hasta ver en el log:
tail -f ~/oww.log  # esperar "INFO: Created TensorFlow Lite XNNPACK delegate for CPU."

# 2. Satélite Wyoming
nohup sh ~/start-satellite.sh </dev/null >/dev/null 2>&1 &

# Verificar:
tail -f ~/wyoming-satellite.log  # debe mostrar "Waiting for wake word"
```

El script `~/.termux/boot/wyoming-satellite-android` arranca el satélite al iniciar Termux,
pero NO arranca openwakeword. Hay que añadirlo al script de boot si se quiere automático.

---

## Diagnóstico rápido

```bash
# ¿Están corriendo los procesos?
ps aux | grep -E "wyoming|openwake" | grep -v grep

# ¿Qué dice el log del satélite?
tail -30 ~/wyoming-satellite.log

# ¿Openwakeword responde?
# Desde el Mac:
nc -z -w 2 192.168.1.129 10401 && echo "OK" || echo "CAÍDO"

# ¿Kiosk server en worker-01?
systemctl --user status kiosk-server

# ¿ADB conectado?
adb -s 192.168.1.129:32906 shell echo ok

# ¿HA puede hacer TTS? (DNS Docker)
docker exec homeassistant python3 -c \
  "import urllib.request; urllib.request.urlopen('https://translate.google.es', timeout=5); print('OK')"
```
