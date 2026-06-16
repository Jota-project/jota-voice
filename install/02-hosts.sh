#!/bin/sh
set -e
source ../lib/output.sh
source ../lib/yaml.sh

HOSTS_FILE="/data/data/com.termux/files/usr/etc/hosts"

_check() {
    local missing=false
    while read -r ip name; do
        grep -q "${ip} ${name}" "$HOSTS_FILE" 2>/dev/null || missing=true
    done << EOF
$(yaml_get_hosts)
EOF
    [ "$missing" = "false" ]
}

_apply() {
    _info "Configurando /etc/hosts..."
    {
        echo "127.0.0.1 localhost"
        echo "::1 ip6-localhost"
        yaml_get_hosts
    } > "$HOSTS_FILE"
    _ok "/etc/hosts configurado"
}

if [ -n "$1" ] && [ "$1" = "--check" ]; then
    _check && _ok "Hosts ya configurados" || exit 1
else
    _apply
fi