"""Fail the build on undefined names anywhere in the sidecar.

`compileall` only checks syntax, so a name that is imported inside one function and
used inside another compiles happily and then raises NameError at runtime — exactly
how `without_honorific` shipped: the reflex spoke its line, the turn died right after,
and turn_done never fired, so the app just hung mid-turn. pyflakes catches it statically
in about a second.
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP = {".venv", "build", "dist", "__pycache__"}

files = [str(p) for p in ROOT.rglob("*.py") if not any(x in p.parts for x in SKIP)]
r = subprocess.run([sys.executable, "-m", "pyflakes", *files],
                   capture_output=True, text=True, cwd=ROOT)

# pyflakes reports plenty that is merely untidy (unused imports, star-imports). Only
# the classes that break at runtime are build-stopping.
FATAL = ("undefined name", "undefined local", "f-string is missing placeholders")
bad = [l for l in r.stdout.splitlines() if any(f in l for f in FATAL)]

print(f"  scanned {len(files)} files")
for line in bad:
    print("  FAIL  " + line)
print("\n" + ("ALL PASS" if not bad else f"{len(bad)} FATAL NAME ERRORS"))
sys.exit(1 if bad else 0)
