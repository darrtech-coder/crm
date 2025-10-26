@echo off
setlocal

:: ============================================================================
:: CreateAndCommit.bat
::
:: Turns the current folder into a Git repository (if it isn't one already),
:: adds all files, and creates a commit.
:: ============================================================================

title Create Git Repo and Commit

:: --- 1. Check if Git is installed and in the PATH ---
echo Checking for Git...
where git >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo ERROR: Git was not found on your system's PATH.
    echo Please install Git and make sure it's added to your PATH.
    echo Download from: https://git-scm.com/downloads
    goto :End
)
echo Git found.
echo.


:: --- 2. Check if this is already a Git repository ---
if exist ".git\" (
    echo This folder is already a Git repository.
    echo Skipping initialization. Proceeding to add and commit...
) else (
    echo This folder is not a Git repository. Initializing...
    git init
    if %errorlevel% neq 0 (
        echo ERROR: Failed to initialize Git repository.
        goto :End
    )
    echo Successfully initialized an empty Git repository.
)
echo.


:: --- 3. Add all files and prompt for a commit message ---
echo Staging all files in the current folder (git add .)...
git add .

echo.
echo Please enter a commit message.
set /p commitMessage="Commit message (press Enter for default): "

:: If the user just pressed Enter, provide a default message
if "%commitMessage%"=="" (
    set commitMessage=Initial commit on %date% %time%
    echo Using default message: "%commitMessage%"
)


:: --- 4. Create the commit ---
echo.
echo Committing changes...
git commit -m "%commitMessage%"

if %errorlevel% neq 0 (
    echo.
    echo ---
    echo NOTE: No changes were detected to commit.
    echo Your working tree is clean.
    echo ---
) else (
    echo.
    echo ---
    echo SUCCESS: Commit has been created successfully!
    echo ---
)


:End
echo.
echo Script finished. Press any key to exit.
pause >nul