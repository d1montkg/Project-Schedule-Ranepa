@echo off
rem Замени путь на папку, куда ты склонировал проект
cd /d "C:\path\to\ranepa-schedule"
".venv\Scripts\python.exe" sync_all.py
exit /b %errorlevel%