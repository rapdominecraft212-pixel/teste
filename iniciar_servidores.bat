@echo off
title KwaiEditor - Iniciando
cd /d "%~dp0"

REM Verificar se ja existe um processo rodando
if exist launcher.pid (
    echo ATENCAO: Ja existe um launcher rodando!
    echo Use "parar_servidores.bat" primeiro.
    echo.
    pause
    exit /b 1
)

REM Garantir que os diretorios existem
if not exist data\upload mkdir data\upload
if not exist data\cortado mkdir data\cortado
if not exist data\editado mkdir data\editado
if not exist data\biblioteca mkdir data\biblioteca

echo ============================================
echo  KwaiEditor - Iniciando servidores
echo ============================================
echo.
echo  Listener (bot Telegram) + Worker (pipeline)
echo.
echo  Pressione Ctrl+C para parar.
echo ============================================
echo.

python scripts\launcher.py

echo.
echo Servidores encerrados.
pause
