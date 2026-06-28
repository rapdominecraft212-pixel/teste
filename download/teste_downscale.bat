@echo off
REM ============================================================
REM  Teste de Downscale FFmpeg - Diagnostico
REM  Gera log em: teste_downscale_LOG.txt
REM ============================================================

setlocal enabledelayedexpansion
chcp 65001 >nul

set "LOG=%~dp0teste_downscale_LOG.txt"
set "KWAIEDITOR=%~dp0"

REM Limpar log anterior
if exist "%LOG%" del "%LOG%"

echo ============================================================ > "%LOG%"
echo  TESTE DOWNSCALE FFmpeg - %date% %time% >> "%LOG%"
echo  Pasta base: %KWAIEDITOR% >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo. >> "%LOG%"

echo --- 1. Versao do ffmpeg --- >> "%LOG%"
ffmpeg -version 2>&1 >> "%LOG%"
echo. >> "%LOG%"

echo --- 2. Procurando videos em data\upload\123456\ --- >> "%LOG%"
set "VIDEO_DIR=%KWAIEDITOR%data\upload\123456"
if not exist "%VIDEO_DIR%" (
    echo ERRO: Pasta nao existe: %VIDEO_DIR% >> "%LOG%"
    echo.
    echo Pasta nao existe: %VIDEO_DIR%
    pause
    exit /b 1
)

set "VIDEO_FOUND="
for %%F in ("%VIDEO_DIR%\*.mp4") do (
    if not defined VIDEO_FOUND (
        set "VIDEO_FOUND=%%F"
    )
)

if not defined VIDEO_FOUND (
    echo ERRO: Nenhum video .mp4 encontrado em %VIDEO_DIR% >> "%LOG%"
    echo.
    echo Nenhum video encontrado em %VIDEO_DIR%
    pause
    exit /b 1
)

echo Video encontrado: %VIDEO_FOUND% >> "%LOG%"
echo Tamanho: %~z1 bytes >> "%LOG%"
echo. >> "%LOG%"

echo --- 3. Informacoes do video (ffmpeg -i) --- >> "%LOG%"
ffmpeg -i "%VIDEO_FOUND%" 2>&1 >> "%LOG%"
echo. >> "%LOG%"

echo --- 4. Testando downscale (scale=-1:360) --- >> "%LOG%"
set "OUTPUT=%KWAIEDITOR%test_downscaled.mp4"
if exist "%OUTPUT%" del "%OUTPUT%"

echo Comando: ffmpeg -y -i "%VIDEO_FOUND%" -vf "scale=-1:360" -preset ultrafast -crf 28 -an "%OUTPUT%" >> "%LOG%"
echo. >> "%LOG%"

ffmpeg -y -i "%VIDEO_FOUND%" -vf "scale=-1:360" -preset ultrafast -crf 28 -an "%OUTPUT%" 2>&1 >> "%LOG%"

echo. >> "%LOG%"
echo --- 5. Resultado --- >> "%LOG%"
if exist "%OUTPUT%" (
    echo Arquivo gerado: %OUTPUT% >> "%LOG%"
    for %%F in ("%OUTPUT%") do echo Tamanho: %%~zF bytes >> "%LOG%"
    if %%~zF EQU 0 (
        echo *** FALHOU: arquivo tem 0 bytes *** >> "%LOG%"
    ) else (
        echo *** SUCESSO: downscale funcionou *** >> "%LOG%"
    )
) else (
    echo *** FALHOU: arquivo nao foi criado *** >> "%LOG%"
)

echo. >> "%LOG%"
echo --- 6. Teste alternativo: scale=-2:360 (forca par) --- >> "%LOG%"
set "OUTPUT2=%KWAIEDITOR%test_downscaled2.mp4"
if exist "%OUTPUT2%" del "%OUTPUT2%"

ffmpeg -y -i "%VIDEO_FOUND%" -vf "scale=-2:360" -preset ultrafast -crf 28 -an "%OUTPUT2%" 2>&1 >> "%LOG%"

echo. >> "%LOG%"
if exist "%OUTPUT2%" (
    for %%F in ("%OUTPUT2%") do echo Tamanho alt: %%~zF bytes >> "%LOG%"
) else (
    echo Arquivo alternativo nao foi criado >> "%LOG%"
)

echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo  FIM DO TESTE - Envie teste_downscale_LOG.txt para o agente >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo ============================================
echo Teste concluido!
echo Log salvo em: %LOG%
echo ============================================
echo.
echo Abra teste_downscale_LOG.txt e envie o conteudo para o agente.
echo.
pause
