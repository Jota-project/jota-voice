#!/bin/sh
# yaml.sh — Parseo de config.yaml
# Uso: source lib/yaml.sh

YAML_FILE="${REPO_DIR}/config.yaml"

yaml_get() {
    local key="$1"
    grep "^${key}:" "$YAML_FILE" 2>/dev/null | sed 's/^[^:]*: *//' | tr -d '"'
}

# yaml_get_nested SECTION KEY — valor de un campo anidado un nivel:
#   section:
#     key: "valor"
yaml_get_nested() {
    local section="$1" key="$2"
    awk -v section="$section" -v key="$key" '
        $0 ~ "^"section":" { in_section=1; next }
        in_section && /^[^[:space:]]/ { in_section=0 }
        in_section && $0 ~ "^[[:space:]]*"key"[[:space:]]*:" {
            sub("^[[:space:]]*"key"[[:space:]]*:[[:space:]]*", "")
            gsub(/"/, "")
            sub(/[[:space:]]*#.*$/, "")
            sub(/[[:space:]]*$/, "")
            print
            exit
        }
    ' "$YAML_FILE" 2>/dev/null
}

yaml_get_hosts() {
    local in_hosts=false
    local ip="" name=""
    grep -A 30 "^hosts:" "$YAML_FILE" 2>/dev/null | while read -r line; do
        case "$line" in
            "hosts:")
                in_hosts=true
                ;;
            "")
                in_hosts=false
                ;;
            *ip:*)
                ip=$(echo "$line" | sed 's/.*ip: *"\([^"]*\)".*/\1/')
                ;;
            *name:*)
                name=$(echo "$line" | sed 's/.*name: *"\([^"]*\)".*/\1/')
                if [ -n "$ip" ] && [ -n "$name" ]; then
                    echo "${ip} ${name}"
                    ip=""; name=""
                fi
                ;;
        esac
    done
}