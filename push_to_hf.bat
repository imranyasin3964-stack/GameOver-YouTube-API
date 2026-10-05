@echo off
title GameOver YouTube API - One-Click Deploy to Hugging Face
color 0b

echo ======================================================================
echo   [GAMEOVER YOUTUBE API] ONE-CLICK PUSH TO HUGGING FACE SPACES
echo ======================================================================
echo.
echo NOTE: Ye script sirf aur sirf Hugging Face (hf main) pe push karegi.
echo       GitHub pe bilkul koi push nahi jayega.
echo.

cd /d "%~dp0"

echo [1/3] Staging all files...
git add .

echo.
echo [2/3] Committing changes...
set "commit_msg="
set /p commit_msg="Enter commit message (Press Enter for default: Update GameOver API): "
if "%commit_msg%"=="" set commit_msg=Update GameOver API
git commit -m "%commit_msg%"

echo.
echo [3/3] Pushing to Hugging Face (hf main)...
git push hf main

echo.
if %ERRORLEVEL% EQU 0 (
    color 0a
    echo ======================================================================
    echo   [SUCCESS] Hugging Face Space pe push ho gaya!
    echo   Space URL: https://huggingface.co/spaces/Imranyasin/gameover-music-bot
    echo ======================================================================
) else (
    color 0c
    echo ======================================================================
    echo   [ERROR] Push fail ho gaya! Internet ya credentials check karein.
    echo ======================================================================
)

echo.
pause
