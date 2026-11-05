@echo off
setlocal
set "VSDEV=C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\Common7\Tools\VsDevCmd.bat"
if not exist "%VSDEV%" (
  echo Visual Studio C++ Build Tools not found. Update VSDEV in build.cmd.
  exit /b 1
)
call "%VSDEV%" -arch=x64 -host_arch=x64
if errorlevel 1 exit /b 1
cd /d "%~dp0"
if exist ".toolchain\microsoft.windows.sdk.cpp\c\Include\10.0.28000.0\um\Windows.h" (
 set "INCLUDE=%~dp0.toolchain\microsoft.windows.sdk.cpp\c\Include\10.0.28000.0\ucrt;%~dp0.toolchain\microsoft.windows.sdk.cpp\c\Include\10.0.28000.0\shared;%~dp0.toolchain\microsoft.windows.sdk.cpp\c\Include\10.0.28000.0\um;%INCLUDE%"
 set "LIB=%~dp0.toolchain\microsoft.windows.sdk.cpp.x64\c\um\x64;%~dp0.toolchain\microsoft.windows.sdk.cpp.x64\c\ucrt\x64;%LIB%"
)
cl /nologo /std:c11 /utf-8 /W4 /O2 /D_CRT_SECURE_NO_WARNINGS main.c core.c gui.c /Fe:gs610_2700.exe /link user32.lib gdi32.lib comdlg32.lib comctl32.lib shell32.lib
exit /b %errorlevel%

