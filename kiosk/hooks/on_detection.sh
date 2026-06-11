#!/data/data/com.termux/files/usr/bin/sh
curl -s -X POST http://192.168.1.109:8766/state \
  -H 'Content-Type: application/json' \
  -d '{"state":"listening","text":""}' &
