@echo off
chcp 65001 >nul
title UniHelp - Instalar (sem .exe)
cd /d "%~dp0"
set "DEST=%LOCALAPPDATA%\UniHelp"

set PY=python
where python >nul 2>nul || set PY=py
for /f "delims=" %%P in ('%PY% -c "import sys;print(sys.executable)"') do set "PYEXE=%%P"
if not defined PYEXE (echo Python nao encontrado. Instala-o em python.org & pause & exit /b 1)
set "PYW=%PYEXE:python.exe=pythonw.exe%"
if not exist "%PYW%" (echo Nao encontrei o pythonw.exe. & pause & exit /b 1)

echo [1/2] A instalar dependencias...
%PY% -m pip install --upgrade customtkinter pillow || (pause & exit /b 1)

echo [2/2] A instalar o UniHelp em %DEST% ...
if not exist "%DEST%" mkdir "%DEST%"
copy /y unihelp.py "%DEST%\" >nul && copy /y comunidade.py "%DEST%\" >nul && copy /y servidor.py "%DEST%\" >nul || (echo Falhou a copiar. & pause & exit /b 1)
copy /y unihelp.ico "%DEST%\" >nul
copy /y desinstalar.bat "%DEST%\" >nul
if exist servidor_url.txt copy /y servidor_url.txt "%DEST%\" >nul

powershell -NoProfile -Command "$w=New-Object -ComObject WScript.Shell; foreach($d in @([Environment]::GetFolderPath('Programs'),[Environment]::GetFolderPath('Desktop'))){ $s=$w.CreateShortcut((Join-Path $d 'UniHelp.lnk')); $s.TargetPath='%PYW%'; $s.Arguments='unihelp.py'; $s.WorkingDirectory='%DEST%'; $s.IconLocation='%DEST%\unihelp.ico'; $s.Description='UniHelp'; $s.Save() }"

set "K=HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\UniHelp"
reg add "%K%" /v DisplayName /d "UniHelp" /f >nul
reg add "%K%" /v DisplayIcon /d "%DEST%\unihelp.ico" /f >nul
reg add "%K%" /v InstallLocation /d "%DEST%" /f >nul
reg add "%K%" /v Publisher /d "UniHelp" /f >nul
reg add "%K%" /v UninstallString /d "\"%DEST%\desinstalar.bat\"" /f >nul

echo.
echo Pronto! O UniHelp esta no menu Iniciar e no Ambiente de trabalho.
start "" "%PYW%" "%DEST%\unihelp.py"
exit /b 0
