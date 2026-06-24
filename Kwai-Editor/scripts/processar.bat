@echo off
title KwaiEditor - Processar Video
cd /d "C:\Users\User\Desktop\video-editor"
python pipeline\runner.py
echo.
echo Pressione ENTER para fechar...
pause > nul
