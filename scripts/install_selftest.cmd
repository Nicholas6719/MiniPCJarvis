@echo off
REM Registers the nightly self-test (03:30, user session). Run once from the real session.
schtasks /Create /TN JARVIS_SELFTEST /TR "\"%~dp0selftest.cmd\"" /SC DAILY /ST 03:30 /F
