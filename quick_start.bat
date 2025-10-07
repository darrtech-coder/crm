@echo off
SETLOCAL

echo === Setting up Python virtual environment ===
where python >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo Python not found. Please ensure Python 3.11+ is installed and added to PATH.
    exit /b 1
)

:: Create venv if missing
if not exist venv (
    python -m venv venv
)

call venv\Scripts\activate


:: Path to Redis (adjust if installed elsewhere)
SET REDIS_EXE=redis-latest\redis-server.exe

echo === Checking Redis ===
tasklist | findstr /I "redis-server.exe" >nul
IF %ERRORLEVEL% EQU 0 (
    echo ✅ Redis is already running.
) ELSE (
    echo ⚠️  Redis is NOT running.
    if exist "%REDIS_EXE%" (
        echo ▶️ Starting bundled Redis...
        START "" "%REDIS_EXE%"
    ) ELSE (
        where redis-server >nul 2>&1
        if %ERRORLEVEL% NEQ 0 (
            echo ❌ Redis not found. Please install Redis and add it to PATH.
        ) else (
            echo ▶️ Attempting to start Redis from PATH...
            start /B redis-server
            ping 127.0.0.1 -n 3 > nul
            echo ✅ Redis started.
        )
    )
)

echo === Running database migrations ===
set FLASK_APP=wsgi.py
set FLASK_ENV=development

:: One initial migration, then upgrade
flask db init || echo (migrations folder already exists)
flask db migrate -m "Add TestCourseRequirement table"
flask db migrate -m "Add TestCourseRequirement"
flask db migrate -m "Add TestPrerequisite model"
flask db upgrade

echo === Starting Flask server ===
flask run --reload --host=127.0.0.1

pause
start quick_start.bat
ENDLOCAL