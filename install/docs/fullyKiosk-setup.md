# FullyKiosk Browser — Setup Manual

FullyKiosk no puede configurarse por ADB o SSH — requiere interacción directa en el teléfono.
Hacer este setup TRAS ejecutar `bootstrap.sh` o `jota-voice setup`.

## Pasos

1. **Abrir FullyKiosk Browser** en el teléfono (o esperar a que el boot hook lo abra)

2. **Start URL**: `http://localhost:8766`
   - Settings → Web Content → Start URL

3. **Microphone access**: **DISABLED** (CRÍTICO)
   - Settings → Device Management → Microphone Access: Off
   - Si está activado, FullyKiosk bloquea el acceso al micrófono para sles-source y OWW deja de funcionar

4. **Autostart on boot**: **DISABLED**
   - Settings → Device Management → Autostart on Boot: Off
   - El boot hook (`~/.termux/boot/jota-voice`) abre FullyKiosk una vez que jota-display está listo

5. **Kiosk mode**: **ENABLED**
   - Settings → Kiosk Mode → Enable Kiosk Mode: On

6. **Screen timeout**: dejar en valores por defecto
   - El control de pantalla lo gestiona `kiosk_server.py` desde el Mac via ADB

## Verificación

Tras el setup, reiniciar el teléfono. El orden esperado:

1. Boot hook arranca (~30s)
2. PulseAudio + sles-source se cargan
3. supervisord arranca oww, jota-display, jota-voice
4. jota-display está listo en puerto 8766 (~30-60s)
5. Boot hook detecta jota-display → abre FullyKiosk automáticamente
6. FullyKiosk carga `http://localhost:8766` → interfaz kiosk visible

## Diagnóstico

- Boot hook no abre FullyKiosk → revisar `~/boot.log`
- Micrófono no funciona → verificar que Microphone Access está **Off** en FullyKiosk
- Pantalla en negro → comprobar `jota-voice status` y `jota-voice logs jota-display`