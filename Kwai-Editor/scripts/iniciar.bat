@echo off
title KwaiEditor - Servidores
cd /d "%~dp0.."

REM Metodo recomendado: usa launcher.py com KeepAwake, PID file e Job Object
python scripts\launcher.py

echo.
echo Servidores encerrados.
timeout /t 3 /nobreak >nul
