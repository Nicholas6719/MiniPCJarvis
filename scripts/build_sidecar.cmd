@echo off
REM Build the Python sidecar. FAILS if any module doesn't compile - PyInstaller
REM otherwise bundles broken modules silently (exit 0) and the app breaks at runtime.
cd /d "%~dp0..\sidecar"
.venv\Scripts\python.exe -m compileall -q . -x "\.venv|build|dist" || (echo COMPILE FAILED & exit /b 1)
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); import main, orchestrator, brain.router, search_brave_web, browser.session" || (echo IMPORT FAILED & exit /b 1)
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe tests\check_names.py || (echo NAME CHECK FAILED & exit /b 1)
.venv\Scripts\python.exe tests\seed_collisions.py || (echo SEED CLASH FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_brain.py || (echo BRAIN TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_audit_fixes.py || (echo AUDIT TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_persona.py || (echo PERSONA TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_facts.py || (echo FACTS TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_remote.py || (echo REMOTE TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_input.py || (echo INPUT TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_shortlist.py || (echo SHORTLIST TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\sleep_coverage.py || (echo SLEEP TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\speech_symbols.py || (echo SPEECH TEST FAILED & exit /b 1)
.venv\Scripts\pyinstaller jarvis-sidecar.spec --noconfirm --distpath dist --workpath build > "%TEMP%\pyi.log" 2>&1 || (echo PYINSTALLER FAILED & exit /b 1)
echo SIDECAR BUILD OK


