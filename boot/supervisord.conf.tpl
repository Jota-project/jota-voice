# boot/supervisord.conf.tpl
# supervisord usa %(ENV_HOME)s para expandir $HOME en tiempo de ejecución.
# install.sh copia este fichero a ~/supervisord.conf.
#
# Orden de arranque (todo arranca en paralelo dentro de cada nivel):
#   pri 1: pulseaudio          — base, sin audio no hay nada
#   pri 2: jota-display        — abre la UI lo antes posible para que FK muestre boot screen
#   pri 5: sles-source-loader  — toma posesión del mic y lo monitoriza
#   pri 6: oww                 — modelo TFLite (tarda ~3 min, no bloquea al resto)
#   pri 7: jota-voice          — cliente principal, depende del mic

[supervisord]
logfile=%(ENV_HOME)s/supervisord.log
pidfile=%(ENV_HOME)s/supervisord.pid
nodaemon=false
minfds=1024

[inet_http_server]
port=127.0.0.1:9001

[supervisorctl]
serverurl=http://127.0.0.1:9001

[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface

[program:pulseaudio]
command=pulseaudio --daemonize=no --exit-idle-time=-1
environment=PULSE_RUNTIME_PATH="%(ENV_HOME)s/.pulse"
stdout_logfile=%(ENV_HOME)s/pulseaudio.log
stderr_logfile=%(ENV_HOME)s/pulseaudio.log
autorestart=true
startsecs=2
priority=1

[program:jota-display]
command=python3 %(ENV_HOME)s/jota-display/server/server.py
directory=%(ENV_HOME)s/jota-display
stdout_logfile=%(ENV_HOME)s/jota-display.log
stderr_logfile=%(ENV_HOME)s/jota-display.log
autorestart=true
startretries=10
startsecs=3
priority=2

[program:sles-source]
command=%(ENV_HOME)s/jota-voice/boot/sles-source-loader.sh
environment=PULSE_RUNTIME_PATH="%(ENV_HOME)s/.pulse"
stdout_logfile=%(ENV_HOME)s/sles-source.log
stderr_logfile=%(ENV_HOME)s/sles-source.log
autorestart=true
startretries=999
startsecs=3
priority=5

[program:oww]
command=%(ENV_HOME)s/oww-venv/bin/python3 -u -m wyoming_openwakeword
    --uri tcp://0.0.0.0:10401
    --model ok_nabu
    --threshold 0.5
    --trigger-level 3
directory=%(ENV_HOME)s
environment=PYTHONUNBUFFERED="1"
stdout_logfile=%(ENV_HOME)s/oww.log
stderr_logfile=%(ENV_HOME)s/oww.log
autorestart=true
startretries=10
startsecs=5
priority=6

[program:jota-voice]
command=%(ENV_HOME)s/jota-voice/.venv/bin/python3
    %(ENV_HOME)s/jota-voice/client/voice_client.py
    %(ENV_HOME)s/jota-voice/config.yaml
directory=%(ENV_HOME)s/jota-voice
environment=PULSE_RUNTIME_PATH="%(ENV_HOME)s/.pulse"
stdout_logfile=%(ENV_HOME)s/jota-voice.log
stderr_logfile=%(ENV_HOME)s/jota-voice.log
autorestart=true
startretries=10
startsecs=5
priority=7