#!/usr/bin/env bash
# monitor_gateway.sh — Monitoriza jota-gateway en tiempo real desde el Mac.
#
# Uso: bash scripts/monitor_gateway.sh
#
# Conecta a green-house por SSH y sigue los logs de jota_gateway,
# filtrando y coloreando por severidad.

set -euo pipefail

HOST="sito@192.168.1.106"
CONTAINER="jota_gateway"

# Colores ANSI
RED='\033[0;31m'
YELLOW='\033[0;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
RESET='\033[0m'

echo -e "${CYAN}=== Monitor jota-gateway (${CONTAINER} en green-house) ===${RESET}"
echo -e "${CYAN}Conectando a ${HOST}...${RESET}"
echo ""

ssh -o ConnectTimeout=10 "${HOST}" "docker logs ${CONTAINER} -f --tail=0 2>&1" | \
while IFS= read -r line; do
    if echo "${line}" | grep -q "ERROR\|Exception\|Traceback\|error"; then
        echo -e "${RED}${line}${RESET}"
    elif echo "${line}" | grep -q "WARNING\|Warning"; then
        echo -e "${YELLOW}${line}${RESET}"
    elif echo "${line}" | grep -q "transcription\|llm_start\|tts_start\|session_start\|done\|Handshake"; then
        echo -e "${GREEN}${line}${RESET}"
    else
        echo "${line}"
    fi
done
