@echo off
chcp 65001 >nul
set PYTHONUTF8=1
title UniHelp - Criar .exe
cd /d "%~dp0"

set PY=python
where python >nul 2>nul || set PY=py

echo [1/3] A instalar dependencias...
%PY% -m pip install --upgrade customtkinter pillow pyinstaller || goto erro

echo.
echo [2/3] A criar o UniHelp.exe (pode demorar 1-2 minutos)...
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed --name UniHelp --icon unihelp.ico ^
  --collect-all customtkinter --hidden-import servidor --hidden-import comunidade unihelp.py || goto erro

echo.
echo [3/3] A preparar a pasta final...
if not exist "UniHelp" mkdir "UniHelp"
copy /y "dist\UniHelp.exe" "UniHelp\UniHelp.exe" >nul
rem o .env (palavras-passe) e a base de dados do servidor NUNCA vao para a pasta que se partilha
del /q "UniHelp\.env" "UniHelp\unihelp_server.db" >nul 2>nul
if exist "servidor_url.txt" copy /y "servidor_url.txt" "UniHelp\servidor_url.txt" >nul

echo.
echo .exe criado. A instalar como aplicacao...
call "%~dp0instalar.bat"
exit /b 0

:erro
echo.
echo Algo falhou. Le a mensagem acima.
pause
exit /b 1
