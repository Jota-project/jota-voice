# nginx — Proxy inverso en green-house

## Estructura de ficheros

```
/etc/nginx/
├── nginx.conf                    # config principal (no tocar)
├── certs/
│   ├── server.crt                # cert mkcert (SANs: green-house.local, localhost, 192.168.1.105)
│   └── server.key
├── includes/
│   └── api-locations.conf        # ← rutas compartidas entre HTTP y HTTPS
└── sites-enabled/
    └── server-hub.conf           # ← único fichero activo
```

`openclaw.conf` fue eliminado (código muerto — `j.alfonsogarre.com` no tenía regla en CF tunnel ni cert válido).

---

## server-hub.conf

Dos bloques `server` que comparten las mismas rutas vía `include`:

```nginx
# HTTP — LAN directo
server {
    listen 80 default_server;
    server_name _;
    include /etc/nginx/includes/api-locations.conf;
}

# HTTPS — Cloudflare Tunnel + LAN con SSL
server {
    listen 443 ssl default_server;
    server_name _;
    ssl_certificate     /etc/nginx/certs/server.crt;
    ssl_certificate_key /etc/nginx/certs/server.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    include /etc/nginx/includes/api-locations.conf;
}
```

No hay redirect 80→443. Justificación:
- Tráfico externo llega cifrado por Cloudflare Tunnel (HTTPS terminado en el edge de CF)
- Tráfico LAN es red de confianza — no necesita SSL

---

## api-locations.conf

Fichero incluido en ambos bloques. Cada `location` hace `proxy_pass` a un puerto interno (loopback).

Los servicios con WebSocket necesitan:
```nginx
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
proxy_buffering off;
```

OpenClaw tiene `proxy_read_timeout 3600s` por sesiones largas de agente.

---

## Certificado SSL

Generado con `mkcert` (desarrollo). No es de confianza pública.  
SANs actuales: `green-house.local`, `greenhouse.local`, `localhost`, `192.168.1.105`

**Nota:** la IP dinámica actual es `192.168.1.106` — el cert tiene `.105` (IP anterior).  
Para HTTPS desde la LAN se recomienda usar `green-house.local` (está en los SANs) o regenerar el cert añadiendo la IP actual.

Para regenerar:
```bash
mkcert -cert-file /etc/nginx/certs/server.crt \
       -key-file  /etc/nginx/certs/server.key \
       green-house.local greenhouse.local localhost \
       192.168.1.106 127.0.0.1
sudo systemctl reload nginx
```

---

## Operaciones habituales

```bash
# Validar config antes de aplicar
sudo nginx -t

# Recargar sin cortar tráfico
sudo systemctl reload nginx

# Ver logs de error en tiempo real
sudo journalctl -u nginx -f

# Añadir un nuevo servicio
# 1. Editar /etc/nginx/includes/api-locations.conf
# 2. nginx -t && systemctl reload nginx
```

---

## IP dinámica

Green-house tiene IP DHCP en `192.168.1.x`. El DNS local resuelve `green-house` automáticamente.  
**Recomendación:** fijar reserva DHCP en el router para la MAC de `eno2` y así evitar que cambie entre reinicios.
