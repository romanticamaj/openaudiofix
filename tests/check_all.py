"""Run every test layer and stop at nothing: a report of all of them, non-zero if any failed.

    python tests/check_all.py [--full]

Types, unit tests, the production build, the real-browser tests, and Python (without the slow tests unless --full).
The browser tests need the build, so a failed build skips them. AFIX_PYTHON names the Python for the browser tests'
server; by default it is the one running this script.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def main():
    full = "--full" in sys.argv[1:]
    npx = "npx.cmd" if os.name == "nt" else "npx"
    npm = "npm.cmd" if os.name == "nt" else "npm"
    marks = "not private and not docker" if full else "not private and not docker and not slow"
    env = dict(os.environ, PYTHONUTF8="1", AFIX_PYTHON=os.environ.get("AFIX_PYTHON", sys.executable))
    steps = [
        ("types", [npx, "tsc", "--noEmit", "-p", "."], WEB),
        ("web", [npm, "test"], WEB),
        ("build", [npm, "run", "build"], WEB),
        ("browser", [npx, "playwright", "test", "--reporter=line"], WEB),
        ("python", [sys.executable, "-m", "pytest", "-q", "-m", marks, "tests"], ROOT),
    ]
    results = {}
    for name, cmd, cwd in steps:
        if name == "browser" and results.get("build") != 0:
            print(f"== {name}: skipped, the build failed", flush=True)
            results[name] = 1
            continue
        print(f"== {name}", flush=True)
        t0 = time.time()
        results[name] = subprocess.run(cmd, cwd=cwd, env=env).returncode
        print(f"== {name}: {'ok' if results[name] == 0 else 'FAILED'} in {time.time() - t0:.0f} s", flush=True)
    print("\n" + "\n".join(f"{'ok    ' if c == 0 else 'FAILED'} {n}" for n, c in results.items()))
    return 1 if any(results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
