@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if /i "%VSCMD_ARG_TGT_ARCH%"=="x64" goto ready
set "K_VS="
for /f "usebackq tokens=*" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "K_VS=%%i"
if not defined K_VS (
 echo Visual Studio C++ build tools and Windows SDK are required to build.
 exit /b 1
)
call "%K_VS%\VC\Auxiliary\Build\vcvarsall.bat" x64 >nul
if errorlevel 1 exit /b 1
:ready
if not exist build mkdir build
if not exist dist mkdir dist
cl /nologo /std:c17 /W4 /O2 /MT /utf-8 /DUNICODE /D_UNICODE /D_CRT_SECURE_NO_WARNINGS /Iinclude /Fo:build\ /Fe:dist\Keithley2700.exe src\main.c src\model.c src\util.c src\serial_port.c src\instrument.c src\storage.c src\acquisition.c src\plot.c src\ui.c /link /SUBSYSTEM:WINDOWS /MANIFEST:EMBED /MANIFESTINPUT:app.manifest user32.lib gdi32.lib shell32.lib ole32.lib comctl32.lib comdlg32.lib
if errorlevel 1 exit /b 1
echo Built dist\Keithley2700.exe
exit /b 0
