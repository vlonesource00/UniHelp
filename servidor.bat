@echo off

chcp 65001 >nul

set PYTHONUTF8=1

title UniHelp - Servidor

cd /d "%~dp0"



set PY=python

where python >nul 2>nul || set PY=py



echo A iniciar o servidor UniHelp... (fecha esta janela ou prime Ctrl+C para parar)

echo.

%PY% servidor.py



echo.

echo O servidor parou. Se foi um erro, le a mensagem acima.

pause
