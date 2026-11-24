@echo off
cd /d "%~dp0"
if exist "C:\ProgramData\anaconda3\python.exe" (
  "C:\ProgramData\anaconda3\python.exe" app.py
) else (
  python app.py
)
if errorlevel 1 pause
