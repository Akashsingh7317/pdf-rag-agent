@echo off
REM Start PDF RAG Agent on http://127.0.0.1:5000 (Windows). Double-click to run.
cd /d "%~dp0"
title PDF RAG Agent

if not exist ".venv\Scripts\python.exe" (
  echo First run: creating the virtual environment and installing packages...
  python -m venv .venv || goto :error
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)

if not exist ".env" (
  copy .env.example .env >nul
  echo Created .env. Add your GROQ_API_KEY to it, save, then run start.bat again.
  notepad .env
  pause
  exit /b 1
)

echo.
echo  PDF RAG Agent is starting at http://127.0.0.1:5000
echo  Keep this window open while you use the app. Close it to stop the server.
echo.
start "" /b cmd /c "timeout /t 5 /nobreak >nul & start http://127.0.0.1:5000"
".venv\Scripts\python.exe" wsgi.py
echo.
echo The server stopped.
pause
exit /b 0

:error
echo.
echo Setup failed. See the messages above.
pause
exit /b 1
