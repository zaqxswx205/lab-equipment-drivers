@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "K_VS="
for /f "usebackq tokens=*" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "K_VS=%%i"
if not defined K_VS exit /b 1
call "%K_VS%\VC\Auxiliary\Build\vcvarsall.bat" x64 >nul
if errorlevel 1 exit /b 1
cl /nologo /std:c17 /W4 /O2 /MT /utf-8 /DUNICODE /D_UNICODE /D_CRT_SECURE_NO_WARNINGS /Iinclude /Fo:build\ /Fe:build\test_core.exe tests\test_core.c src\model.c src\util.c src\serial_port.c src\instrument.c src\storage.c src\acquisition.c src\plot.c /link user32.lib gdi32.lib shell32.lib ole32.lib
if errorlevel 1 exit /b 1
build\test_core.exe
if errorlevel 1 exit /b 1
call build.bat
if errorlevel 1 exit /b 1
start "" /wait dist\Keithley2700.exe --smoke-test
if errorlevel 1 exit /b 1
type dist\smoke_output\smoke_result.txt
exit /b 0
