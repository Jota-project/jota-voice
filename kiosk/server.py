#!/usr/bin/env python3
"""
Servidor kiosk para wyoming-satellite.
- GET  /          → index.html
- POST /state     → actualiza estado y lo emite a todos los clientes SSE
- GET  /events    → Server-Sent Events para el browser
"""
import json
import threading
import queue
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

BASE_DIR = Path(__file__).parent
STATE = {"state": "idle", "text": "", "ts": 0}
CLIENTS: list[queue.SimpleQueue] = []
CLIENTS_LOCK = threading.Lock()

AUTO_SLEEP_SECONDS = 8  # segundos tras mostrar respuesta → pantalla off


def broadcast(data: dict) -> None:
    STATE.update(data)
    STATE["ts"] = time.time()
    msg = "data: " + json.dumps(STATE) + "\n\n"
    with CLIENTS_LOCK:
        dead = []
        for q in CLIENTS:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            CLIENTS.remove(q)


def notify_mac(endpoint: str) -> None:
    """Llama al servidor ADB en el Mac (best-effort)."""
    import urllib.request
    try:
        urllib.request.urlopen(f"http://192.168.1.105:8090/{endpoint}", data=b"", timeout=2)
    except Exception:
        pass


def schedule_sleep(delay: float) -> None:
    def _sleep():
        time.sleep(delay)
        if STATE["state"] == "response":
            broadcast({"state": "idle", "text": ""})
            notify_mac("sleep")
    threading.Thread(target=_sleep, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silenciar logs HTTP

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            html = (BASE_DIR / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        elif self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            q: queue.SimpleQueue = queue.SimpleQueue()
            with CLIENTS_LOCK:
                CLIENTS.append(q)

            # Estado actual al conectar
            try:
                self.wfile.write(("data: " + json.dumps(STATE) + "\n\n").encode())
                self.wfile.flush()
            except Exception:
                with CLIENTS_LOCK:
                    CLIENTS.remove(q)
                return

            while True:
                try:
                    msg = q.get(timeout=25)
                    self.wfile.write(msg.encode())
                    self.wfile.flush()
                except queue.Empty:
                    # keepalive
                    try:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                    except Exception:
                        break
                except Exception:
                    break

            with CLIENTS_LOCK:
                try:
                    CLIENTS.remove(q)
                except ValueError:
                    pass
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/state":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
            except Exception:
                self.send_response(400)
                self.end_headers()
                return

            new_state = data.get("state", "idle")
            broadcast(data)

            if new_state == "listening":
                notify_mac("wake")
            elif new_state == "response":
                schedule_sleep(AUTO_SLEEP_SECONDS)

            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 8766), Handler)
    print("Kiosk server en http://0.0.0.0:8766/")
    server.serve_forever()
