#!/data/data/com.termux/files/usr/bin/sh
DISPLAY_URL="${1:-$(cat ~/.jota-display-url 2>/dev/null || echo 'http://127.0.0.1:8766')}"
curl -s -X POST "$DISPLAY_URL/state" \
  -H 'Content-Type: application/json' \
  -d '{"state":"listening","text":""}' &