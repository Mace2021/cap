@echo off
REM Runs C.A.P. tests, rebuilds dist\CAP, and downloads the recommended local model.
cd /d "%~dp0"
echo ============ 1/3  Running tests ============
".venv\Scripts\python.exe" -m unittest discover -s tests -v > update_log.txt 2>&1
type update_log.txt | findstr /R /C:"^Ran " /C:"^OK" /C:"^FAILED"
echo ============ 2/3  Rebuilding the app (close C.A.P. first) ============
taskkill /IM CAP.exe /F >nul 2>&1
".venv\Scripts\python.exe" build.py >> update_log.txt 2>&1
if errorlevel 1 (echo BUILD FAILED - see update_log.txt) else (echo Build finished: dist\CAP\CAP.exe)
echo ============ 3/3  Downloading Qwen 3.5 9B for Ollama ============
ollama pull qwen3.5:9b
echo OLLAMA_PULL_EXIT=%errorlevel% >> update_log.txt
ollama list >> update_log.txt 2>&1
echo ALL_DONE >> update_log.txt
echo.
echo Done. You can close this window.
pause
