@echo off
chcp 65001 >nul
title Corrigir nomes dos PDFs ja arquivados

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
echo  Corrigir nomes dos PDFs ja arquivados
echo  de:   2925071234567800019955...pdf
echo  para: EMITENTE - NUMERO.pdf
echo ============================================
echo.
echo PASSO 1 de 2 - previa. Nada sera alterado agora.
echo.

python "%SCRIPT%" --renomear
if errorlevel 1 goto :erro

echo.
echo ============================================
set /p RESPOSTA="Aplicar estes nomes? (S = sim, qualquer outra tecla = nao): "
if /i not "%RESPOSTA%"=="S" goto :cancelado

echo.
echo PASSO 2 de 2 - renomeando...
echo.
python "%SCRIPT%" --renomear --aplicar
if errorlevel 1 goto :erro

echo.
echo Pronto. Se algum nome tiver ficado errado, rode:
echo    python contas.py --desfazer-renomear
echo.
pause
exit /b 0

:cancelado
echo.
echo Cancelado - nenhum arquivo foi alterado.
echo.
pause
exit /b 0

:erro
echo.
echo *** O script terminou com erro.
echo.
pause
exit /b 1
