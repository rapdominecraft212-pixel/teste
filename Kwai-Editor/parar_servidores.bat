@echo off
title KwaiEditor - Parando e Resetando
cd /d "%~dp0"

echo ============================================
echo  KwaiEditor - RESET COMPLETO
echo ============================================
echo.

REM ---- PASSO 1: Matar todos os processos Python do projeto ----
echo [1/4] Parando servidores...

if exist launcher.pid (
    for /f "usebackq delims=" %%p in (`type launcher.pid`) do (
        taskkill /F /T /PID %%p >nul 2>&1
    )
    del launcher.pid 2>nul
    echo  Launcher encerrado.
) else (
    echo  PID nao encontrado. Buscando processos...
)

REM Matar qualquer python rodando listener.py ou worker.py
taskkill /F /FI "WINDOWTITLE eq *listener*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq *worker*" >nul 2>&1

echo  Aguardando processos finalizarem...
timeout /t 3 /nobreak >nul

REM ---- PASSO 2: Deletar banco de dados (historico, jobs, estados) ----
echo.
echo [2/4] Limpando banco de dados...

if exist jobs.sqlite3 (
    del /q /f jobs.sqlite3
    echo  Database removido.
) else (
    echo  Nenhum banco encontrado.
)

REM ---- PASSO 3: Deletar todos os videos e dados temporarios ----
echo.
echo [3/4] Limpando videos e dados temporarios...

if exist data\upload (
    rmdir /s /q data\upload
    echo  Upload limpo.
)
if exist data\cortado (
    rmdir /s /q data\cortado
    echo  Cortado limpo.
)
if exist data\editado (
    rmdir /s /q data\editado
    echo  Editado limpo.
)
if exist data\biblioteca (
    rmdir /s /q data\biblioteca
    echo  Biblioteca limpa.
)

REM Recriar diretorios vazios (estrutura limpa)
mkdir data\upload 2>nul
mkdir data\cortado 2>nul
mkdir data\editado 2>nul
mkdir data\biblioteca 2>nul

REM ---- PASSO 4: Concluido ----
echo.
echo [4/4] Reset concluido.
echo.
echo ============================================
echo  RESET COMPLETO!
echo  Sistema limpo e pronto para reiniciar.
echo ============================================
echo.
echo Pressione ENTER para fechar...
pause > nul
