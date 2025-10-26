@echo off
setlocal

:: ============================================================================
:: PublishToGitHub.bat
::
:: Turns the folder into a Git repo, creates a commit, and pushes it to
:: a specific GitHub repository.
:: ============================================================================

title Publish to GitHub

:: --- Configuration ---
set "remoteUrl=https://github.com/darrtech-coder/crm"


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
    echo Skipping initialization.
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


:: --- 3. Stage all files and prompt for a commit message ---
echo Staging all files in the current folder (git add .)...
git add .

echo.
echo Please enter a commit message.
set /p commitMessage="Commit message (press Enter for default): "

if "%commitMessage%"=="" (
    set "commitMessage=Initial commit on %date% %time%"
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
    echo Your working tree is clean. Nothing to push.
    echo ---
    goto :End
)

echo.
echo ---
echo SUCCESS: Commit has been created successfully!
echo ---
echo.


:: --- 5. Configure Remote and Push to GitHub ---
echo Configuring remote repository "origin"...
:: This checks if 'origin' exists. If it does, it updates the URL. If not, it adds it.
git remote -v | find "origin" >nul
if %errorlevel% equ 0 (
    git remote set-url origin %remoteUrl%
) else (
    git remote add origin %remoteUrl%
)
echo Remote 'origin' is set to: %remoteUrl%
echo.

:: Get the current branch name. If it's a brand new repo, it might be empty, so default to 'main'.
echo Determining current branch name...
for /f "tokens=*" %%i in ('git branch --show-current') do set "currentBranch=%%i"
if "%currentBranch%"=="" set "currentBranch=main"
echo.

echo Pushing branch '%currentBranch%' to GitHub...
echo (A login window may appear if you are not authenticated)
git push -u origin %currentBranch%

if %errorlevel% equ 0 (
    echo.
    echo ---
    echo SUCCESS: Your code has been published to GitHub!
    echo You can view it at: %remoteUrl%
    echo ---
) else (
    echo.
    echo ERROR: The push to GitHub failed.
    echo Please check the following:
    echo  1. You have an internet connection.
    echo  2. The remote repository exists and is not empty with a different history.
    echo  3. You have the correct permissions for the repository.
    echo  4. You authenticated correctly in the pop-up window.
)


:End
echo.
echo Script finished. Press any key to exit.
pause >nul
