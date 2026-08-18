@echo off
chcp 65001 >nul
title Reprocessar PDFs pendentes

set "PASTA=%~dp0"
set "SCRIPT=contas.py"

cd /d "%PASTA%"
if not exist "%SCRIPT%" (
    echo ERRO: %SCRIPT% nao encontrado em:
    echo    %PASTA%
    pause
    exit /b 1
)

echo ============================================
echo  Reprocessar PDFs que ficaram sem vencimento
echo  (a pasta _PENDENTES, por padrao)
echo ============================================
echo.
echo Quem tiver vencimento impresso no PDF vai direto para a
echo pasta da data. O resto abre na janela de revisao.
echo.

python "%SCRIPT%" --reprocessar
if errorlevel 1 goto :erro

echo.
pause
exit /b 0

:erro
echo.
echo *** O script terminou com erro.
echo.
pause
exit /b 1
