@echo off
rem One-command backup to GitHub (requires Nona proxy on port 65532)
cd /d "%~dp0"
set HTTP_PROXY=http://127.0.0.1:65532
set HTTPS_PROXY=http://127.0.0.1:65532
echo [1/3] Staging changes...
git add -A
echo [2/3] Committing...
git commit -m "chore: auto backup %date% %time%"
if %errorlevel% neq 0 echo [info] nothing to commit, skipping
echo [3/3] Pushing to GitHub...
git push
echo.
echo Done. Check https://github.com/whatthefact/debug-debug-backup
pause
