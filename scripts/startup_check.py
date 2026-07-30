"""Check that the entry point still starts on both transports, with and
without the event feed, and that the receiver route is actually mounted.

Each case runs the real `main()` in a subprocess and is killed after a moment -
we are checking that startup works, not serving traffic.

    python scripts/startup_check.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def env(**overrides: str) -> dict:
    e = dict(os.environ)
    e["PYTHONPATH"] = str(ROOT / "src")
    e.pop("BITRIX_EVENT_TOKEN", None)
    e.pop("BITRIX_PULL_CHANNEL", None)
    e.update(overrides)
    return e


def run(label: str, args: list[str], overrides: dict, *, probe: str | None = None,
        seconds: float = 6.0) -> bool:
    proc = subprocess.Popen(
        [str(PY), "-m", "bitrix_mcp", *args],
        cwd=ROOT, env=env(**overrides),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    ok_probe = True
    try:
        time.sleep(seconds)
        alive = proc.poll() is None
        if probe and alive:
            # POST, not GET: the receiver only accepts POST, so a GET would
            # answer 405 and prove nothing about whether it works.
            request = urllib.request.Request(
                probe, data=b"event=PROBE",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST")
            try:
                with urllib.request.urlopen(request, timeout=5) as resp:
                    code = resp.status
            except urllib.error.HTTPError as exc:
                code = exc.code
            except Exception as exc:  # noqa: BLE001
                code = f"unreachable: {type(exc).__name__}"
            # 403 is the expected answer: the route is mounted and refuses a
            # delivery that carries no valid application_token.
            ok_probe = code == 403
            print(f"     probe POST {probe} -> {code} (403 = mounted and fail-closed)")
    finally:
        proc.terminate()
        try:
            _out, errtext = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            _out, errtext = proc.communicate()

    good = alive and ok_probe
    print(("PASS " if good else "FAIL ") + label)
    if not good:
        print("   stderr:", (errtext or "").strip()[-600:])
    elif errtext and "Traceback" in errtext:
        print("   note: traceback in stderr:", errtext.strip()[-400:])
    return good


def main() -> None:
    results = [
        run("stdio, no event feed (unchanged behaviour)", [], {}),
        run("stdio + pull channel", [], {"BITRIX_PULL_CHANNEL": "1"}),
        run("http, no event feed", ["--http", "--port", "8731"], {}),
        run("http + receiver mounted", ["--http", "--port", "8732"],
            {"BITRIX_EVENT_TOKEN": "startup-check-token"},
            probe="http://127.0.0.1:8732/b24/events"),
        run("http + receiver + pull channel", ["--http", "--port", "8733"],
            {"BITRIX_EVENT_TOKEN": "startup-check-token", "BITRIX_PULL_CHANNEL": "1"},
            probe="http://127.0.0.1:8733/b24/events"),
    ]
    print("\n" + ("ALL CLEAR" if all(results) else "SOMETHING BROKE"))
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
