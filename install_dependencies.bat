@echo off
REM Install GPS Disruption Tracker dependencies
REM sqlite3 is included with Python's standard library — no pip install needed.

cd /d "%~dp0"

if exist ".venv\Scripts\pip.exe" (
    .venv\Scripts\pip.exe install -r requirements.txt
    echo.
    echo Dependencies installed.
    echo.
    echo Run the dashboard with:
    echo   .venv\Scripts\python.exe -m streamlit run app.py
) else (
    pip install -r requirements.txt
    echo.
    echo Dependencies installed.
    echo.
    echo Run the dashboard with:
    echo   python -m streamlit run app.py
)
