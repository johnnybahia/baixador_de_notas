@echo off
chcp 65001 >nul
title Buscar notas por chave

set "PASTA=%~dp0"
cd /d "%PASTA%"

if not exist "baixador.py" (
    echo ERRO: baixador.py nao encontrado em:
    echo    %PASTA%
    pause
    exit /b 1
)

if not exist "chaves.txt" copy "chaves.txt.exemplo" "chaves.txt" >nul 2>&1
if not exist "chaves.txt" echo # Cole aqui as chaves, uma por linha> "chaves.txt"

echo ============================================
echo  Buscar notas por chave de acesso
echo ============================================
echo.
echo Vou abrir o chaves.txt no Bloco de Notas.
echo Cole as chaves (uma por linha, ate 20), SALVE e FECHE o Bloco de Notas.
echo.
pause

notepad "chaves.txt"

echo.
echo Buscando...
echo.
python baixador.py --chaves-de "chaves.txt"

echo.
pause
