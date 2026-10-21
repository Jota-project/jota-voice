# OpenClaw — Server Deployment

## Server Facts

| Item | Value |
|------|-------|
| Hostname | `mi-servidor` |
| LAN IP | `<IP_SERVIDOR>` (dynamic — local DNS resolves `mi-servidor`) |
| External | `mi-servidor.mi-dominio.com` via Cloudflare Tunnel |
| SSH | `ssh.mi-dominio.com` via Cloudflare Tunnel |
| User | `mi-usuario` (sudo requires password — no NOPASSWD) |
| OS | Ubuntu 24.04 LTS headless |

## OpenClaw Process

```bash
# Running as launchd/systemd user service
node ~/.nvm/versions/node/v22.22.2/lib/node_modules/openclaw/dist/index.js gateway --port 18789

# Status
openclaw gateway status

# Logs
journalctl --user -u openclaw-gateway -f    # if systemd
# or check ~/.openclaw/logs/
```

## Config Location

`~/.openclaw/openclaw.json`

Auth token: `gateway.auth.token` — required for all REST API calls.  
Get token: `openclaw doctor --generate-gateway-token`

## Network Exposure

| Path | Protocol | Accessible from |
|------|----------|-----------------|
| `127.0.0.1:18789` | WebSocket + HTTP REST | Loopback only (direct) |
| `http://mi-servidor/api/openclaw/` | HTTP | LAN only (nginx proxy) |
| `http://mi-servidor/api/gateway/v1/` | HTTP | LAN only (jota-gateway bridge) |
| `https://mi-servidor.mi-dominio.com/api/openclaw/` | HTTPS | External via Cloudflare |

## nginx Proxy

Config: `/etc/nginx/includes/api-locations.conf`  
Proxy: `location /api/openclaw/ { proxy_pass http://127.0.0.1:18789/; ... }`

To add nginx → OpenClaw calls need Bearer token, either:
- Add `proxy_set_header Authorization "Bearer <token>";` in nginx (not recommended — hardcoded)
- Use `trusted-proxy` mode in OpenClaw (recommended — loopback auto-approves)
- Let jota-gateway inject the token programmatically

## All Services on the Server

| Service | Port | Listen | Status |
|---------|------|--------|--------|
| nginx | 80, 443 | 0.0.0.0 | ✅ |
| OpenClaw gateway | 18789 | loopback | ✅ |
| jota-transcriber (STT) | 8003 | 0.0.0.0 | ✅ |
| jota_db_api | 8002 | 0.0.0.0 | ✅ |
| jota-gateway (BFF) | 8004 | 0.0.0.0 | ⚠️ needs OpenClaw bridge |
| jota-speaker (TTS) | 8005 | — | ❌ stopped |
| Ollama | 11434 | 0.0.0.0 | ✅ |
| jota-orchestrator | 8000 | — | ✅ |

## Cloudflare Tunnel Config

`/etc/cloudflared/config.yml`

```yaml
tunnel: <TUNNEL_UUID>
ingress:
  - hostname: mi-servidor.mi-dominio.com
    service: https://127.0.0.1:443
    originRequest:
      noTLSVerify: true
  - hostname: ssh.mi-dominio.com
    service: ssh://127.0.0.1:22
  - service: http_status:404
```

## SSL Certificate

Self-signed mkcert cert. SANs: `mi-servidor.local`, `localhost`, `<IP_SERVIDOR>`

Regenerate if needed:
```bash
mkcert -cert-file /etc/nginx/certs/server.crt \
       -key-file  /etc/nginx/certs/server.key \
       mi-servidor.local localhost <IP_SERVIDOR> 127.0.0.1
sudo systemctl reload nginx
```
