@echo off
title Parando servidores KwaiEditor
cd /d "%~dp0.."

echo Parando servidores KwaiEditor...

if exist launcher.pid (
    for /f "usebackq delims=" %%p in (`type launcher.pid`) do (
        taskkill /F /T /PID %%p >nul 2>&1
    )
    del launcher.pid 2>nul
    echo Launcher encerrado.
) else (
    echo PID nao encontrado. Tentando pelo nome...
    taskkill /F /FI "WINDOWTITLE eq *KwaiEditor*" >nul 2>&1
    taskkill /F /FI "WINDOWTITLE eq *listener*" >nul 2>&1
    taskkill /F /FI "WINDOWTITLE eq *worker*" >nul 2>&1
)

echo.
echo Servidores encerrados.
timeout /t 2 /nobreak >nul
