@echo off
chcp 65001 >nul
title UniHelp - Instalar
set "DEST=%LOCALAPPDATA%\UniHelp"
set "SRC=%~dp0UniHelp"
if not exist "%SRC%\UniHelp.exe" set "SRC=%~dp0"
if not exist "%SRC%\UniHelp.exe" (
  echo Nao encontrei o UniHelp.exe. Corre primeiro o build_exe.bat.
  pause & exit /b 1
)

echo A instalar o UniHelp em %DEST% ...
taskkill /im UniHelp.exe /f >nul 2>nul
if not exist "%DEST%" mkdir "%DEST%"
copy /y "%SRC%\UniHelp.exe" "%DEST%\UniHelp.exe" >nul || (echo Falhou a copiar. & pause & exit /b 1)
copy /y "%~dp0unihelp.ico" "%DEST%\unihelp.ico" >nul 2>nul
copy /y "%~dp0desinstalar.bat" "%DEST%\desinstalar.bat" >nul 2>nul
rem endereco do servidor da comunidade (o .env e a base de dados do servidor ficam so no servidor)
if exist "%SRC%\servidor_url.txt" (copy /y "%SRC%\servidor_url.txt" "%DEST%\servidor_url.txt" >nul) else if exist "%~dp0servidor_url.txt" copy /y "%~dp0servidor_url.txt" "%DEST%\servidor_url.txt" >nul

rem atalhos no menu Iniciar e no Ambiente de trabalho
powershell -NoProfile -Command "$w=New-Object -ComObject WScript.Shell; foreach($d in @([Environment]::GetFolderPath('Programs'),[Environment]::GetFolderPath('Desktop'))){ $s=$w.CreateShortcut((Join-Path $d 'UniHelp.lnk')); $s.TargetPath='%DEST%\UniHelp.exe'; $s.WorkingDirectory='%DEST%'; $s.IconLocation='%DEST%\UniHelp.exe,0'; $s.Description='UniHelp'; $s.Save() }"

rem aparece em Definicoes > Aplicacoes instaladas
set "K=HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\UniHelp"
reg add "%K%" /v DisplayName /d "UniHelp" /f >nul
reg add "%K%" /v DisplayIcon /d "%DEST%\UniHelp.exe" /f >nul
reg add "%K%" /v InstallLocation /d "%DEST%" /f >nul
reg add "%K%" /v Publisher /d "UniHelp" /f >nul
reg add "%K%" /v UninstallString /d "\"%DEST%\desinstalar.bat\"" /f >nul

echo.
echo Pronto! O UniHelp ja esta no menu Iniciar e no Ambiente de trabalho.
start "" "%DEST%\UniHelp.exe"
exit /b 0
