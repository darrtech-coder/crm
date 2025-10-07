@echo off
:: --- Configuration ---
setlocal

set REPO_URL=https://github.com/darrtech-coder/crm.git
set LOCAL_PATH=D:\Documents\dryrun\zip\1
set BRANCH=main
set COMMIT_MSG=Initial commit

:: --- Begin process ---
echo.
echo Navigating to folder: %LOCAL_PATH%
cd /d "%LOCAL_PATH%"
if errorlevel 1 (
    echo ❌ Failed to navigate to %LOCAL_PATH%
    pause
    exit /b 1
)

:: Initialize git repository (if not already initialized)
if not exist ".git" (
    echo Initializing new git repository...
    git init
) else (
    echo Git repository already initialized.
)

:: Add or update remote
git remote remove origin >nul 2>&1
git remote add origin %REPO_URL%
echo ✅ Remote set to: %REPO_URL%

:: Stage files
echo Adding all files...
git add .

:: Commit changes
echo Committing...
git commit -m "%COMMIT_MSG%" || echo (Skipped commit if nothing new)

:: Rename branch and push
git branch -M %BRANCH%
echo Pushing to GitHub...
git push -u origin %BRANCH% --force

echo.
echo ✅ All done! Files uploaded successfully.
pause