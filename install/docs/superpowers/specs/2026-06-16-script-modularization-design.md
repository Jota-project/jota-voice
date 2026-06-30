# Script Modularization Design

## Context

The `install.sh` script (196 lines) is too long and does too many things. Scripts have hardcoded values (IPs, URLs) that should come from `config.yaml` or `devices/*.env`.

## Goals

1. **Separation of concerns** — each script does one thing
2. **Reusability** — shared libraries for parsing YAML and output helpers
3. **Testability** — small functions that can be unit tested
4. **No hardcoded values** — all config comes from `config.yaml` or `devices/*.env`

## Structure

```
boot/
  lib/
    android.sh      # Android/Termux helpers
  hook.sh           # Boot hook, uses boot/lib/android.sh

lib/
  yaml.sh           # Parse config.yaml (reused by install and CLI)
  output.sh         # _ok, _info, _err helpers (reused everywhere)

install/
  01-packages.sh
  02-hosts.sh
  03-venv.sh
  04-oww.sh
  05-supervisord.sh
  06-boot.sh
  07-smoke-test.sh
install.sh           # Runner: sources lib/*.sh, executes install/*.sh

kiosk/
  deploy.sh          # Deploys kiosk UI, uses devices/*.env
  hooks/
    on_detection.sh
    on_transcript.sh
    on_synthesize.sh
    on_stt_start.sh
```

## boot/lib/android.sh

```sh
android_ensure_sshd()      # Ensure sshd is running
android_wait_pulseaudio()  # Wait for PulseAudio to be ready (max 30s)
android_warmup_mic()       # Mic warm-up with termux-microphone-record
android_load_sles_source() # Load module-sles-source
android_set_dns()         # Configure /etc/resolv.conf (router + 8.8.8.8)
android_save_adb_port()    # Save ADB port to ~/adb_port.txt
android_open_fullykiosk()  # Open FullyKiosk
```

## lib/yaml.sh

Provides functions to read from `config.yaml`:

- `yaml_get <key>` — get a top-level value
- `yaml_get_hosts` — get the hosts array as `ip name` pairs

Example usage:
```sh
source lib/yaml.sh
GATEWAY_HOST=$(yaml_get gateway.host)
yaml_get_hosts | while read ip name; do
  echo "$ip $name"
done
```

## lib/output.sh

```sh
_ok()   { echo "  ✓ $*"; }
_info() { echo "  → $*"; }
_err()  { echo "  ✗ $*" >&2; }
_fail() { echo "  ✗ $*" >&2; exit 1; }
```

## install/N scripts

Each script:
- Sources `lib/output.sh`
- Sources `lib/yaml.sh`
- Has a `_check()` function to detect if already done
- Has a `_apply()` function to do the work
- `_apply()` is only called if `_check()` returns false

Example:
```sh
#!/bin/sh
source ../lib/output.sh

_check() {
  command -v supervisord >/dev/null 2>&1
}

_apply() {
  _info "Installing supervisor..."
  pip install -q supervisor
  _ok "supervisord installed"
}

if _check; then
  _ok "supervisord already installed"
else
  _apply
fi
```

## install.sh runner

```sh
#!/bin/sh
set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

source "$REPO_DIR/lib/output.sh"

for step in install/*.sh; do
  echo ""
  echo "=== $(basename "$step") ==="
  . "$step"
done
```

## kiosk/deploy.sh changes

- Loads device from `devices/*.env` (arg or auto-detect)
- Uses `$PHONE_HOST`, `$PHONE_PORT`, `$PHONE_PASS`
- No hardcoded values

## kiosk/hooks changes

- Read display URL from `~/.jota-display-url` (created by install.sh)
- Or receive as argument from Python caller

## Config sources

| Value | Source |
|-------|--------|
| `gateway.host`, `gateway.port`, etc. | `config.yaml` → `lib/yaml.sh` |
| `oww.host`, `oww.port` | `config.yaml` → `lib/yaml.sh` |
| `display.url` | `config.yaml` → `lib/yaml.sh` |
| `hosts[]` | `config.yaml` → `lib/yaml.sh` → `/etc/hosts` |
| `PHONE_HOST`, `PHONE_PORT`, `PHONE_PASS` | `devices/*.env` |
| `PHONE_DIR` | `devices/*.env` |

## init command updates

The `jota-voice init` command should:

1. Copy `config.example.yaml` → `config.yaml` if not exists
2. Ask if user wants to add hosts (IP + name pairs)
3. Ask if user wants to customize `display.url` (default: `http://127.0.0.1:8766`)
4. Create `devices/<name>.env` with device config
5. Verify SSH connection

## Hardcoded values to fix

| File | Hardcoded | Fix |
|------|-----------|-----|
| `kiosk/hooks/*.sh` | `http://192.168.1.109:8766/state` | Read from `~/.jota-display-url` |
| `kiosk/deploy.sh` | IP, PORT, PASS | Use `devices/*.env` |
| `boot/hook.sh` | `192.168.1.1` (DNS) | Read from config.yaml |

## Commit plan

1. Create `lib/` with `output.sh` and `yaml.sh`
2. Create `install/` with staged scripts
3. Refactor `install.sh` as runner
4. Refactor `boot/hook.sh` to use `boot/lib/android.sh`
5. Update `kiosk/deploy.sh` to use `devices/*.env`
6. Update `kiosk/hooks/` to read display URL from file
7. Update `jota-voice init` to ask about display.url
