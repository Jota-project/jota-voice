#!/data/data/com.termux/files/usr/bin/sh
# sles-source-loader — dueño del micrófono Android.
# Carga module-sles-source, valida que está capturado de verdad,
# monitoriza y recupera ante auto-suspend de PulseAudio o robo del HAL.
#
# Estados del mic (lo que validamos):
#   1. Módulo cargado:           pactl list modules short | grep sles-source
#   2. Fuente en estado RUNNING: pactl list sources short | grep OpenSL_ES_source | grep -v SUSPENDED
#   3. HAL responde con audio:   parecord 2s + RMS > 5
#   Las tres deben cumplirse para declarar el mic "capturado".
#
# Política de recuperación:
#   - Suspendida < 30s: esperar a que reanude sola
#   - Suspendida > 30s o estado INVALID: descargar y recargar módulo
#   - Módulo ausente o carga falla: warmup + reintentar carga

LOG="$HOME/sles-source.log"
SVC="sles-source"
. "$HOME/jota-voice/boot/lib/notify.sh"

# Configuración
WARMUP_TIMEOUT=12
LOAD_RETRIES=5
LOAD_RETRY_DELAY=20
RMS_THRESHOLD=5            # ruido de fondo basta
TEST_DURATION=2            # segundos de grabación para RMS
SUSPEND_GRACE=30           # segundos esperando a que reanude sola
MONITOR_INTERVAL=10        # segundos entre chequeos

log()  { echo "$(date '+%H:%M:%S') $*" >> "$LOG"; }
notify_load() { notify "$SVC" "$*"; }

# Devuelve número de consumers reales del mic OpenSL_ES_source.
# En Termux, parec lee directamente del source sin crear source-output
# (rarity de PulseAudio sobre Android), así que comprobamos tanto
# source-outputs como procesos parec vivos en la fuente correcta.
mic_consumers() {
    local so=0 pr=0
    [ -n "$(pactl list source-outputs short 2>/dev/null)" ] && so=$(pactl list source-outputs short 2>/dev/null | wc -l | tr -d ' ')
    pr=$(pgrep -af 'parec.*OpenSL_ES_source' 2>/dev/null | wc -l | tr -d ' ')
    echo $(( so + pr ))
}

# Espera activa hasta que PulseAudio responda
wait_pulseaudio() {
    log "Esperando PulseAudio..."
    until pactl info >/dev/null 2>&1; do sleep 2; done
    log "PulseAudio listo"
}

# Espera activa hasta que Termux:API responda (necesaria para el warmup)
wait_termux_api() {
    log "Esperando Termux:API..."
    until timeout 8 termux-battery-status >/dev/null 2>&1; do
        sleep 5
    done
    log "Termux:API lista"
}

# Warmup del mic — solicita acceso al HAL con timeout
warmup_mic() {
    log "Warmup del mic..."
    timeout "$WARMUP_TIMEOUT" termux-microphone-record -d 1 2>/dev/null || true
    sleep 2
    timeout 5 termux-microphone-record -q 2>/dev/null || true
    sleep 1
}

# Test combinado: fuente RUNNING + HAL responde con audio real
test_mic() {
    # (1) módulo cargado
    pactl list modules short 2>/dev/null | grep -q module-sles-source || return 1

    # (2) fuente existe (RUNNING/IDLE válidos — SUSPENDED=ok si hay consumidor, sino malo)
    local src_state
    src_state=$(pactl list sources short 2>/dev/null | awk '$2=="OpenSL_ES_source"{print $NF}')
    case "$src_state" in
        RUNNING|IDLE) ;;
        SUSPENDED)
            # SUSPENDED solo es problema si NO hay consumidores del mic
            # (clientes pa como parec, jota-voice, etc.)
            mic_consumers | grep -q '^0$' && return 2
            ;;
        *) return 2 ;;
    esac

    # (3) HAL da audio real — parecord 2s + RMS
    local wav="$HOME/.sles-test.wav"
    rm -f "$wav"
    timeout "$((TEST_DURATION + 3))" parecord \
        --device=OpenSL_ES_source \
        --channels=1 --rate=16000 --format=s16le \
        --file-format=wav "$wav" 2>/dev/null
    [ -f "$wav" ] || return 3

    local rms
    rms=$(python3 -c "
import wave, struct, sys
try:
    w = wave.open('$wav','rb')
    n = w.getnframes()
    raw = w.readframes(n)
    samples = struct.unpack('<${TEST_DURATION}000h', raw[:${TEST_DURATION}000*2])
    rms = (sum(s*s for s in samples)/len(samples))**0.5
    print(int(rms))
except Exception as e:
    print(0)
" 2>/dev/null)
    rm -f "$wav"
    [ -n "$rms" ] && [ "$rms" -ge "$RMS_THRESHOLD" ] && return 0
    return 4
}

# Carga sles-source con warmup y reintentos
load_sles() {
    local attempt=1
    while [ $attempt -le $LOAD_RETRIES ]; do
        log "Intento $attempt/$LOAD_RETRIES"
        notify_load "🔄 Intento $attempt/$LOAD_RETRIES: warmup + carga"

        warmup_mic

        if pactl load-module module-sles-source >/dev/null 2>&1; then
            sleep 1
            local rc=0
            test_mic || rc=$?
            if [ $rc -eq 0 ]; then
                log "sles-source capturada (intento $attempt)"
                notify_ok "$SVC" "✅ Mic capturado — listo"
                return 0
            fi
            log "Cargada pero test falló (rc=$rc)"
            pactl unload-module module-sles-source 2>/dev/null || true
            [ $rc -eq 4 ] && log "Módulo responde pero RMS=0 — HAL no da audio"
            [ $rc -eq 2 ] && log "Fuente no pasó a RUNNING"
        else
            log "pactl load-module falló"
        fi

        attempt=$(( attempt + 1 ))
        [ $attempt -le $LOAD_RETRIES ] && sleep "$LOAD_RETRY_DELAY"
    done

    log "ERROR: no se pudo cargar tras $LOAD_RETRIES intentos"
    notify_err "$SVC" "❌ Fallo tras $LOAD_RETRIES intentos — ver sles-source.log"
    return 1
}

# Monitor: detecta auto-suspend y robo de HAL, recarga si hace falta
monitor() {
    log "Monitorizando (cada ${MONITOR_INTERVAL}s)..."
    local suspended_since=0

    while true; do
        sleep "$MONITOR_INTERVAL"

        local src_state
        src_state=$(pactl list sources short 2>/dev/null | awk '$2=="OpenSL_ES_source"{print $NF}')

        case "$src_state" in
            RUNNING|IDLE)
                suspended_since=0
                ;;
            SUSPENDED)
                # Si hay consumidor activo (jota-voice via parec, etc.), no es problema —
                # PulseAudio suspende automáticamente cuando todos los consumers cierran.
                if mic_consumers | grep -q '^0$'; then
                    if [ $suspended_since -eq 0 ]; then
                        suspended_since=$(date +%s)
                        log "Fuente suspendida (sin consumers) — esperando ${SUSPEND_GRACE}s"
                        notify_warn "$SVC" "🟡 Suspendida sin consumers — esperando resume"
                    fi
                    local now
                    now=$(date +%s)
                    if [ $((now - suspended_since)) -gt $SUSPEND_GRACE ]; then
                        log "Suspendida >${SUSPEND_GRACE}s sin consumers — recargando"
                        notify_warn "$SVC" "🔄 Recargando módulo"
                        pactl unload-module module-sles-source 2>/dev/null || true
                        sleep 2
                        if ! load_sles; then
                            log "Reload falló — saliendo para que supervisord reinicie"
                            notify_err "$SVC" "❌ Reload falló — reiniciando loader"
                            exit 1
                        fi
                        suspended_since=0
                    fi
                else
                    suspended_since=0
                fi
                ;;
            *)
                log "Estado fuente inesperado: '$src_state' — recargando"
                notify_warn "$SVC" "⚠ Estado raro: $src_state — recargando"
                pactl unload-module module-sles-source 2>/dev/null || true
                exit 1
                ;;
        esac
    done
}

# ── Arranque ─────────────────────────────────────────────────
log "=== sles-source-loader iniciado ==="
notify "$SVC" "🔄 Cargando..."

wait_pulseaudio
wait_termux_api

# Si ya está cargado y validado, saltar a monitor
if test_mic 2>/dev/null; then
    log "sles-source ya estaba capturada"
    notify_ok "$SVC" "✅ Ya capturado al arranque"
else
    load_sles || exit 1
fi

monitor