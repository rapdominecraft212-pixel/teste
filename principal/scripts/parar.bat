@echo off
title Parando servidores KwaiEditor
cd /d "%~dp0.."

if exist launcher.pid (
    for /f "usebackq delims=" %%p in (`type launcher.pid`) do (
        taskkill /F /PID %%p >nul 2>&1
    )
    del launcher.pid 2>nul
    echo Launcher encerrado.
) else (
    echo PID nao encontrado. Tentando matar pelo nome...
    taskkill /F /IM python.exe >nul 2>&1
)

echo Servidores encerrados.
timeout /t 2 /nobreak >nul
