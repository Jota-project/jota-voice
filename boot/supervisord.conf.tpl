# boot/supervisord.conf.tpl
# supervisord usa %(ENV_HOME)s para expandir $HOME en tiempo de ejecución.
# install.sh copia este fichero a ~/supervisord.conf.

[supervisord]
logfile=%(ENV_HOME)s/supervisord.log
pidfile=%(ENV_HOME)s/supervisord.pid
nodaemon=false

[unix_http_server]
file=%(ENV_HOME)s/supervisor.sock

[supervisorctl]
serverurl=unix:///%(ENV_HOME)s/supervisor.sock

[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface

[program:oww]
command=%(ENV_HOME)s/oww-venv/bin/python3 -m wyoming_openwakeword
    --uri tcp://0.0.0.0:10401
    --preload-model ok_nabu
    --threshold 0.1
directory=%(ENV_HOME)s
stdout_logfile=%(ENV_HOME)s/oww.log
stderr_logfile=%(ENV_HOME)s/oww.log
autorestart=true
startretries=10
startsecs=5
priority=10

[program:jota-display]
command=python3 %(ENV_HOME)s/jota-display/server/server.py
directory=%(ENV_HOME)s/jota-display
stdout_logfile=%(ENV_HOME)s/jota-display.log
stderr_logfile=%(ENV_HOME)s/jota-display.log
autorestart=true
startretries=10
startsecs=3
priority=20

[program:jota-voice]
command=%(ENV_HOME)s/jota-voice/.venv/bin/python3
    %(ENV_HOME)s/jota-voice/client/voice_client.py
    %(ENV_HOME)s/jota-voice/config.yaml
directory=%(ENV_HOME)s/jota-voice
stdout_logfile=%(ENV_HOME)s/jota-voice.log
stderr_logfile=%(ENV_HOME)s/jota-voice.log
autorestart=true
startretries=10
startsecs=5
priority=30