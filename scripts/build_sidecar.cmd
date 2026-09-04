@echo off
REM Build the Python sidecar. FAILS if any module doesn't compile - PyInstaller
REM otherwise bundles broken modules silently (exit 0) and the app breaks at runtime.
cd /d "%~dp0..\sidecar"
REM A BUILD MUST NOT TOUCH HIS LIVE DATABASE. The import check below constructs
REM MemoryStore at module scope, which opens %APPDATA%\JARVIS\jarvis.db — his
REM real memories, transcript, reminders and audit log. Building should never be
REM able to write to those, and on 2026-09-02 it could not even READ one: the
REM build failed with "database disk image is malformed" against a copy that had
REM nothing to do with the change being built. Every gate already honours
REM JARVIS_DB (os.environ.setdefault), so setting it once here points the whole
REM build at a throwaway file and his data is out of reach for the duration.
if not exist "%TEMP%\jarvis-gate" mkdir "%TEMP%\jarvis-gate" 2>nul
set JARVIS_DB=%TEMP%\jarvis-gate\gate.db
.venv\Scripts\python.exe -m compileall -q . -x "\.venv|build|dist" || (echo COMPILE FAILED & exit /b 1)
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); import main, orchestrator, brain.router, search_brave_web, browser.session" || (echo IMPORT FAILED & exit /b 1)
set PYTHONIOENCODING=utf-8
REM Before anything else: no scheduled task of mine may still be armed. Three of
REM those have escaped now, and one of them woke him at midnight. The build is the
REM last thing that runs before a deploy, so it is where this gets caught.
.venv\Scripts\python.exe tests\check_stray_tasks.py || (echo STRAY SCHEDULED TASK & exit /b 1)
.venv\Scripts\python.exe tests\check_names.py || (echo NAME CHECK FAILED & exit /b 1)
.venv\Scripts\python.exe tests\seed_collisions.py || (echo SEED CLASH FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_canon_erasure.py || (echo CANON ERASURE FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_evolution_wiring.py || (echo EVOLUTION WIRING FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_volatile.py || (echo VOLATILE FACTS FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_projects.py || (echo PROJECTS FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_location_health.py || (echo LOCATION/HEALTH FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_vision_analyze.py || (echo VISION ANALYZE FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_biometric.py || (echo BIOMETRIC FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_fabrication.py || (echo FABRICATION FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_meshio.py || (echo MESH PARSE FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_holo.py || (echo HOLOGRAM FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_assembly.py || (echo ASSEMBLY FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_workspace.py || (echo WORKSPACE FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_feature_set.py || (echo FEATURE SET FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_intent.py || (echo INTENT FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_offline.py || (echo OFFLINE FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_meshshot.py || (echo MESHSHOT FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_components.py || (echo COMPONENTS FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_progressive.py || (echo PROGRESSIVE RENDER FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_uia_types.py || (echo UIA CONTROL TYPES FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_dark_lift.py || (echo DARK LIFT FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_typed_yes.py || (echo TYPED CONFIRMATION FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_scout.py || (echo SCOUT FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_features.py || (echo FEATURES FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_colours.py || (echo COLOURS FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_printcheck.py || (echo PRINT CHECK FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_holo_control.py || (echo HOLO CONTROL FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_render.py || (echo RENDER QUEUE FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_hand_control.py || (echo HAND CONTROL FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_brain.py || (echo BRAIN TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_audit_fixes.py || (echo AUDIT TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_persona.py || (echo PERSONA TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_persona_sync.py || (echo PERSONA SYNC FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_facts.py || (echo FACTS TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_remote.py || (echo REMOTE TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_input.py || (echo INPUT TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_shortlist.py || (echo SHORTLIST TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_endpoint.py || (echo ENDPOINT TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_dictation.py || (echo DICTATION TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_clarify.py || (echo CLARIFY TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_output_watch.py || (echo OUTPUT WATCH TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_voice_note.py || (echo VOICE NOTE TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_reminders.py || (echo REMINDER TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_delivery.py || (echo DELIVERY TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_handoff.py || (echo HANDOFF TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_significance.py || (echo SIGNIFICANCE TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_briefing.py || (echo BRIEFING TEST FAILED & exit /b 1)

.venv\Scripts\python.exe tests\test_analyst.py || (echo ANALYST TEST FAILED & exit /b 1)

.venv\Scripts\python.exe tests\test_newsroom.py || (echo NEWSROOM TEST FAILED & exit /b 1)

.venv\Scripts\python.exe tests\test_audio_io.py || (echo AUDIO IO TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_audio_deaf_output.py || (echo AUDIO DEAF OUTPUT TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_speech_pipeline.py || (echo SPEECH PIPELINE TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_isolation.py || (echo ISOLATION TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_chase_budget.py || (echo CHASE BUDGET TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_camera.py || (echo CAMERA TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_presence.py || (echo PRESENCE TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_objects.py || (echo OBJECTS TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_identity.py || (echo IDENTITY TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_hands.py || (echo HANDS TEST FAILED & exit /b 1)

.venv\Scripts\python.exe tests\test_linkguard.py || (echo LINKGUARD TEST FAILED & exit /b 1)

.venv\Scripts\python.exe tests\test_market_resilience.py || (echo MARKET RESILIENCE TEST FAILED & exit /b 1)

.venv\Scripts\python.exe tests\test_wake_display.py || (echo WAKE DISPLAY TEST FAILED & exit /b 1)

.venv\Scripts\python.exe tests\test_deaf_watch.py || (echo DEAF WATCH TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_launchable.py || (echo LAUNCHABLE TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_db_repair.py || (echo DB REPAIR TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_write_lock.py || (echo WRITE LOCK TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_reminder_flood.py || (echo REMINDER FLOOD TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_reminder_voice.py || (echo REMINDER VOICE TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\test_delivery_budget.py || (echo DELIVERY BUDGET TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\sleep_coverage.py || (echo SLEEP TEST FAILED & exit /b 1)
.venv\Scripts\python.exe tests\speech_symbols.py || (echo SPEECH TEST FAILED & exit /b 1)
.venv\Scripts\pyinstaller jarvis-sidecar.spec --noconfirm --distpath dist --workpath build > "%TEMP%\pyi.log" 2>&1 || (echo PYINSTALLER FAILED & exit /b 1)
echo SIDECAR BUILD OK


