#!/usr/bin/env python3
"""
Servidor ADB en el Mac. Recibe peticiones del teléfono y controla la pantalla.
POST /wake  → adb shell input keyevent KEYCODE_WAKEUP
POST /sleep → adb shell input keyevent KEYCODE_SLEEP
"""
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler

ADB = "/opt/homebrew/bin/adb"
TRANSPORT = "-t 1"


def adb(keyevent: str) -> None:
    subprocess.run([ADB, *TRANSPORT.split(), "shell", "input", "keyevent", keyevent],
                   capture_output=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[adb-screen] {self.path}")

    def do_POST(self):
        if self.path == "/wake":
            adb("KEYCODE_WAKEUP")
        elif self.path == "/sleep":
            adb("KEYCODE_SLEEP")
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 8090), Handler)
    print("ADB screen controller en :8090")
    server.serve_forever()
