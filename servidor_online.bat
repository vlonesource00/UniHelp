@echo off
chcp 65001 >nul
set PYTHONUTF8=1
title UniHelp - Servidor online (Cloudflare Tunnel)
cd /d "%~dp0"

set PY=python
where python >nul 2>nul || set PY=py
where cloudflared >nul 2>nul || (
  echo Falta o cloudflared. Instala com:  winget install --id Cloudflare.cloudflared
  echo Depois fecha e volta a abrir esta janela.
  pause & exit /b 1
)

rem o servidor so ouve neste PC; o tunel leva as ligacoes da internet ate ele (com https)
set HOST=127.0.0.1
set TRUST_PROXY=1
echo A iniciar o servidor UniHelp numa janela propria...
start "UniHelp - Servidor" cmd /k %PY% servidor.py
timeout /t 3 >nul

echo.
echo A abrir o tunel. Procura abaixo o endereco https://....trycloudflare.com
echo e escreve-o em "Servidor: ..." no ecra de entrada da app, em todos os PCs.
echo (O endereco muda sempre que o tunel reinicia. Fecha esta janela para desligar.)
echo.
cloudflared tunnel --url http://localhost:8765
pause
