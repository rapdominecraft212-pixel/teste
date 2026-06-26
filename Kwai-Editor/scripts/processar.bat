@echo off
title KwaiEditor - Processar Video
cd /d "%~dp0.."
python pipeline\simple.py
echo.
echo Pressione ENTER para fechar...
pause > nul
