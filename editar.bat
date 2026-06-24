@echo off
title KWAI Editor
cd /d "%~dp0"
python pipeline\editor.py
echo.
echo Pressione ENTER para fechar...
pause > nul
