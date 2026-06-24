@echo off
title KwaiEditor - Servidores
cd /d "%~dp0.."

:: Minimiza automaticamente
if "%MINIMIZED%"=="" set MINIMIZED=1 & start /MIN cmd /c "%~f0" & exit /b

:: Inicia os servidores (mesmo console — morrem quando a janela fechar)
start /B python bot\listener.py >nul 2>&1
start /B python bot\worker.py >nul 2>&1

echo.
echo  KwaiEditor rodando em segundo plano
echo  Feche esta janela para parar tudo
echo.

:: Mantém a janela aberta
timeout /t 86400 /nobreak >nul
