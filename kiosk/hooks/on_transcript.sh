#!/data/data/com.termux/files/usr/bin/sh
TEXT="$1"
PAYLOAD=$(printf '{"state":"thinking","text":"%s"}' "$(echo "$TEXT" | sed 's/"/\\"/g')")
curl -s -X POST http://192.168.1.109:8766/state \
  -H 'Content-Type: application/json' \
  -d "$PAYLOAD" &
