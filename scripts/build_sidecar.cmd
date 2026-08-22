@echo off
REM Build the Python sidecar. FAILS if any module doesn't compile — PyInstaller
REM otherwise bundles broken modules silently (exit 0) and the app breaks at runtime.
cd /d "%~dp0..\sidecar"
.venv\Scripts\python.exe -m compileall -q . -x "\.venv|build|dist" || (echo COMPILE FAILED & exit /b 1)
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); import main, orchestrator, brain.router, search_brave_web, browser.session" || (echo IMPORT FAILED & exit /b 1)
.venv\Scripts\pyinstaller jarvis-sidecar.spec --noconfirm --distpath dist --workpath build > "%TEMP%\pyi.log" 2>&1 || (echo PYINSTALLER FAILED & exit /b 1)
echo SIDECAR BUILD OK
