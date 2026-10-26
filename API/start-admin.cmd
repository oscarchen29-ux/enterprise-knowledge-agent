@echo off
rem Start the database admin web UI (Flask) on 127.0.0.1:7001. It has no login, so it only listens on this PC.
cd /d "%~dp0"
python -m admin
