@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ============================================================
echo   CAPTURA DE ESTRUTURA DO QWEN AI
echo ============================================================
echo.

REM Tenta encontrar Python (python, python3, py launcher)
set "PYTHON_CMD="
for %%P in (python python3 py) do (
    where %%P >nul 2>&1
    if not errorlevel 1 (
        if not defined PYTHON_CMD set "PYTHON_CMD=%%P"
    )
)

if not defined PYTHON_CMD (
    echo ERRO: Python nao encontrado no PATH.
    echo.
    echo Opcoes:
    echo   1. Instale Python 3.10+ de https://python.org
    echo   2. Se ja tem Python, adicione ao PATH do Windows
    echo   3. Abra a Microsoft Store e instale "Python 3.11"
    echo.
    pause
    exit /b 1
)

echo Python encontrado: %PYTHON_CMD%
%PYTHON_CMD% --version
echo.

REM Verifica se Playwright esta instalado
%PYTHON_CMD% -c "import playwright" >nul 2>&1
if errorlevel 1 (
    echo Instalando Playwright...
    %PYTHON_CMD% -m pip install playwright
    if errorlevel 1 (
        echo ERRO: Falha ao instalar Playwright.
        echo Tente manualmente: %PYTHON_CMD% -m pip install playwright
        pause
        exit /b 1
    )
    echo Instalando Chromium para Playwright...
    %PYTHON_CMD% -m playwright install chromium
    if errorlevel 1 (
        echo AVISO: Falha ao instalar Chromium.
        echo Tentando usar Google Chrome instalado no sistema...
    )
)

REM Verifica se o chrome_profile existe
if not exist "Playwright\chrome_profile" (
    echo AVISO: Pasta Playwright\chrome_profile nao encontrada.
    echo O Chrome vai abrir sem perfil salvo - voce precisara fazer login no Qwen.
    echo.
)

echo.
echo ------------------------------------------------------------
echo  INSTRUCOES:
echo ------------------------------------------------------------
echo  1. O Chrome vai abrir com o site do Qwen AI.
echo  2. Se precisar, faca login normalmente.
echo  3. Faca o processo completo:
echo     - Clique no seletor de modo (mode-select-open)
echo     - Escolha "Upload attachment"
echo     - Selecione um video
echo     - Digite o prompt (pode ser qualquer um)
echo     - Clique em enviar
echo     - Aguarde a resposta completar
echo  4. Quando terminar, FECHE O NAVEGADOR (X).
echo  5. O script vai compactar tudo num ZIP automaticamente.
echo.
echo  Os logs estao sendo salvos em: captura_logs\
echo.
echo ------------------------------------------------------------
echo.
pause

echo.
echo Iniciando captura...
echo.
%PYTHON_CMD% capturar_qwen.py

if errorlevel 1 (
    echo.
    echo ============================================================
    echo  ERRO durante a captura. Verifique as mensagens acima.
    echo ============================================================
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Captura concluida com sucesso!
echo ============================================================
echo.
echo  Arquivo final esta em:
echo    captura_logs\captura_qwen_*.zip
echo.
echo  Envie esse ZIP de volta para analise.
echo.
pause
