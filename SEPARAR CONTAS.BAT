@echo off
chcp 65001 >nul
title Classificador de Notas por Vencimento

rem %~dp0 = a pasta onde este .BAT esta, entao ele funciona
rem em qualquer computador, sem caminho fixo
set "PASTA=%~dp0"
set "SCRIPT=contas.py"

cd /d "%PASTA%"
if errorlevel 1 (
    echo ERRO: nao consegui acessar a pasta:
    echo    %PASTA%
    pause
    exit /b 1
)

if not exist "%SCRIPT%" (
    echo ERRO: %SCRIPT% nao encontrado em:
    echo    %PASTA%
    pause
    exit /b 1
)

echo ============================================
echo  Classificador de Notas por Vencimento
echo  %date% %time%
echo ============================================
echo.

python "%SCRIPT%"
set "CODIGO=%errorlevel%"

echo.
if not "%CODIGO%"=="0" (
    echo *** O script terminou com erro. Codigo: %CODIGO%
) else (
    echo Concluido.
)
echo.
pause
