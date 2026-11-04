@echo off
setlocal
cd /d "%~dp0.."
call build.bat
if errorlevel 1 exit /b 1
for /f "usebackq tokens=*" %%I in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "GS610_TEST_VS=%%I"
call "%GS610_TEST_VS%\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1
cl /nologo /std:c11 /W4 /WX /utf-8 tests\test_sweep.c /Fo:build\test_sweep.obj /Fe:build\test_sweep.exe /link /incremental:no
if errorlevel 1 exit /b 1
build\test_sweep.exe
if errorlevel 1 exit /b 1
cl /nologo /std:c11 /W4 /WX /utf-8 tests\test_gui.c /Fo:build\test_gui.obj /Fe:build\test_gui.exe /link /incremental:no user32.lib gdi32.lib comdlg32.lib setupapi.lib advapi32.lib uuid.lib
if errorlevel 1 exit /b 1
build\test_gui.exe
exit /b %errorlevel%
