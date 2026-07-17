@echo off
cd /d "%~dp0"
call venv\Scripts\activate.bat
waitress-serve --host=127.0.0.1 --port=5000 app:app
