#!/data/data/com.termux/files/usr/bin/sh
TEXT="$1"
DISPLAY_URL="${2:-$(cat ~/.jota-display-url 2>/dev/null || echo 'http://127.0.0.1:8766')}"
PAYLOAD=$(printf '{"state":"response","text":"%s"}' "$(echo "$TEXT" | sed 's/"/\\"/g')")
curl -s -X POST "$DISPLAY_URL/state" \
  -H 'Content-Type: application/json' \
  -d "$PAYLOAD" &