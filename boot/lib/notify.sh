#!/data/data/com.termux/files/usr/bin/sh
# notify.sh — notificaciones Termux para supervisord y servicios
# Uso: . notify.sh; notify "pulseaudio" "arrancando"
#      notify_ok | notify_warn | notify_err (mismo ID, reemplaza la anterior)

# IDs reservados por servicio (rango 1000-1099)
_notify_id() { echo $((1000 + $(echo "$1" | cksum | cut -c1-3) % 100)); }

# Iconos Unicode por nivel
_ICON_INFO="🔵"
_ICON_OK="🟢"
_ICON_WARN="🟡"
_ICON_ERR="🔴"

# title, body [, priority]
notify() {
    local svc="$1" body="$2" pri="${3:-high}"
    [ -z "$TERMUX_VERSION" ] && return 0   # no es Termux, no hacer nada
    command -v termux-notification >/dev/null || return 0
    termux-notification \
        --id "$(_notify_id "$svc")" \
        --title "${_ICON_INFO} ${svc}" \
        --content "$body" \
        --priority "$pri" \
        --vibrate 0 \
        --on-going 2>/dev/null
}

notify_ok()   { local svc="$1" body="$2"; notify "$svc" "$body" low; }
notify_warn() { local svc="$1" body="$2"; notify "$svc" "$body" high; }
notify_err()  { local svc="$1" body="$2"; notify "$svc" "$body" max; }

# Borra la notificación de un servicio
notify_clear() {
    local svc="$1"
    [ -z "$TERMUX_VERSION" ] && return 0
    command -v termux-notification >/dev/null || return 0
    termux-notification-remove "$(_notify_id "$svc")" 2>/dev/null
}