@echo off
chcp 65001 >nul
title UniHelp - Desinstalar
set "DEST=%LOCALAPPDATA%\UniHelp"
echo Isto remove o UniHelp deste computador.
set /p DEL=Apagar tambem a base de dados do servidor (contas e publicacoes)? [s/N]: 
taskkill /im UniHelp.exe /f >nul 2>nul
powershell -NoProfile -Command "foreach($d in @([Environment]::GetFolderPath('Programs'),[Environment]::GetFolderPath('Desktop'))){ Remove-Item (Join-Path $d 'UniHelp.lnk') -ErrorAction SilentlyContinue }"
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\UniHelp" /f >nul 2>nul
if /i "%DEL%"=="s" (
  start "" /min cmd /c "ping 127.0.0.1 -n 3 >nul & rd /s /q "%DEST%""
) else (
  start "" /min cmd /c "ping 127.0.0.1 -n 3 >nul & del /q "%DEST%\*.exe" "%DEST%\*.py" "%DEST%\*.bat" "%DEST%\*.ico" "%DEST%\*.log" "%DEST%\.env" "%DEST%\servidor_url.txt" &rd /s /q "%DEST%\__pycache__""
)
echo UniHelp removido.
timeout /t 2 >nul
