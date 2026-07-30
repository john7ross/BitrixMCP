"""Scan git for leaked credentials before anything is pushed.

Reads the real secrets from .env, then searches the working tree, the index and
the ENTIRE commit history for them. Nothing secret is ever printed - only where
it was found.

History matters as much as the current state: removing a file in a later commit
does not remove it from the objects a push would upload.

    python scripts/git_secret_scan.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def git(*args: str) -> str:
    result = subprocess.run(["git", "-C", str(ROOT), *args],
                            capture_output=True, text=True, errors="replace")
    return result.stdout


def secrets() -> dict[str, str]:
    """Named secrets from .env. The webhook token is split out of the URL."""
    found: dict[str, str] = {}
    env = ROOT / ".env"
    if not env.exists():
        sys.exit("no .env to take secrets from")
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if not value:
            continue
        if "WEBHOOK" in key.upper():
            found["webhook URL"] = value
            token = value.rstrip("/").rsplit("/", 1)[-1]
            if len(token) >= 8:
                found["webhook token"] = token
        elif "TOKEN" in key.upper():
            found[key] = value
    return found


def main() -> None:
    values = secrets()
    print(f"scanning for {len(values)} secret(s): {', '.join(values)}\n")

    tracked = git("ls-files").splitlines()
    print(f"tracked files: {len(tracked)}")
    for risky in (".env", ".env.bak", "bitrix_events.sqlite3"):
        state = "TRACKED" if risky in tracked else "not tracked"
        flag = "  <-- MUST NOT BE" if risky in tracked else ""
        print(f"  {risky:<24} {state}{flag}")

    ignored = [f for f in (".env", ".env.bak", ".docs-cache/") 
               if git("check-ignore", "-q", f) is not None]
    print()

    clean = True
    for name, value in values.items():
        print(f"-- {name} --")

        # 1. working tree + index (tracked files only)
        hits = git("grep", "-l", "--cached", "-F", value).strip()
        if hits:
            clean = False
            print(f"   FOUND in tracked files: {hits.splitlines()}")
        else:
            print("   tracked files: clean")

        # 2. full history, any branch
        commits = git("log", "--all", "--oneline", f"-S{value}").strip()
        if commits:
            clean = False
            print("   FOUND in history:")
            for line in commits.splitlines():
                print(f"      {line}")
        else:
            print("   commit history: clean")
        print()

    untracked = [f for f in git("status", "--porcelain").splitlines()
                 if f.startswith("??")]
    risky_untracked = [f[3:] for f in untracked
                       if re.search(r"\.env|sqlite3|\.bak$", f)]
    if risky_untracked:
        print(f"untracked but present (fine, just do not add them): {risky_untracked}")

    print("\n" + ("CLEAN - no secret found in tracked files or history"
                  if clean else
                  "LEAK - do NOT push; the secret is in git and must be rotated"))
    sys.exit(0 if clean else 1)


if __name__ == "__main__":
    main()
