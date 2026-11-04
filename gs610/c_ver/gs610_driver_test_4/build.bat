@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
where cl >nul 2>nul
if errorlevel 1 (
  for /f "usebackq tokens=*" %%I in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "GS610_VS=%%I"
  if not defined GS610_VS (
    echo 未找到 Visual Studio C++ 编译工具，请安装 Desktop development with C++。
    exit /b 1
  )
)
if defined GS610_VS call "%GS610_VS%\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 exit /b 1
if not exist build mkdir build
cl /nologo /std:c11 /W4 /WX /O2 /utf-8 gs610_sweep.c /Fo:build\gs610_sweep.obj /Fe:gs610_sweep.exe /link /incremental:no
if errorlevel 1 exit /b 1
cl /nologo /std:c11 /W4 /WX /O2 /utf-8 gs610_gui.c /Fo:build\gs610_gui.obj /Fe:gs610_gui.exe /link /subsystem:windows /incremental:no user32.lib gdi32.lib comdlg32.lib setupapi.lib advapi32.lib uuid.lib
exit /b %errorlevel%
