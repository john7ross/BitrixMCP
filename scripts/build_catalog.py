"""Build the REST method catalog from Bitrix24's official documentation source.

The portal's own `methods` call is NOT a usable inventory - verified against a
live portal, it omits tasks.task.*, crm.item.*, catalog.*, sale.order.* and
tasks.api.scrum.* even though every one of them answers when called. `method.get`
is no better: it reported task.stages.get as non-existent while the actual call
returned data.

So the catalog comes from the documentation repository that generates
apidocs.bitrix24.ru. Regenerating is a script, not a manual edit, because a
catalog that silently falls behind the API is worse than no catalog: it would
make the agent confident about a signature that has changed.

    python scripts/build_catalog.py                 # clone/update, then build
    python scripts/build_catalog.py --docs <path>   # use an existing checkout

Output: src/bitrix_mcp/data/catalog.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

DOCS_REPO = "https://github.com/bitrix24/b24restdocs.git"
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHECKOUT = ROOT / ".docs-cache"
OUTPUT = ROOT / "src" / "bitrix_mcp" / "data" / "catalog.json"

H1 = re.compile(r"^#\s+(.*)$")
METHOD_IN_TITLE = re.compile(r"\b([a-z][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)+)\s*$")
SCOPE_LINE = re.compile(r"^>\s*Scope:\s*(.+)$", re.M)
TICKED = re.compile(r"\[`([^`]+)`\]|`([^`]+)`")
PARAM_ROW = re.compile(r"^\|\|\s*\*\*([A-Za-z_][A-Za-z0-9_.\[\]]*)\*\*(\*+)?\s*$")
TYPE_CELL = re.compile(r"^\[?`([^`]+)`")

# Prose that the docs put where a scope name goes.
SCOPE_NOISE = {"moduleId", "depending on the embedding location",
               "depending on the placement", "depending on the placement location",
               "depending on the integration point"}
SCOPE_ALIAS = {"sonet": "sonet_group", "basic": "main"}


def ensure_docs(path: Path) -> Path:
    if (path / "api-reference").is_dir():
        print(f"updating {path}")
        subprocess.run(["git", "-C", str(path), "pull", "--ff-only"],
                       check=False, capture_output=True)
        return path
    print(f"cloning {DOCS_REPO} -> {path}")
    result = subprocess.run(
        ["git", "clone", "--depth", "1", DOCS_REPO, str(path)],
        capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"clone failed: {result.stderr.strip()[:500]}\n"
                 f"If GitHub is unreachable from this machine, download the repo "
                 f"manually and pass --docs <path>.")
    return path


def method_name(md_path: Path, title: str) -> str | None:
    hit = METHOD_IN_TITLE.search(title.strip())
    if hit:
        return hit.group(1)
    # Some pages carry a prose title; the filename still encodes the method.
    stem = md_path.stem
    if stem in ("index", "b24-toc") or "-" not in stem:
        return None
    return stem.replace("-", ".")


def parse_params(text: str) -> list[dict]:
    lines = text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        low = line.lower()
        if low.startswith("#") and ("parameter" in low or "параметр" in low):
            start = idx
            break
    if start is None:
        return []
    out: list[dict] = []
    in_table = False
    i = start
    while i < len(lines):
        line = lines[i].rstrip()
        if line.startswith("#|"):
            in_table = True
            i += 1
            continue
        if in_table and line.startswith("|#"):
            break
        if in_table:
            row = PARAM_ROW.match(line)
            if row:
                name, required = row.group(1), bool(row.group(2))
                ptype, desc = "", ""
                if i + 1 < len(lines):
                    cell = lines[i + 1]
                    tmatch = TYPE_CELL.match(cell.strip())
                    if tmatch:
                        ptype = tmatch.group(1)
                    parts = cell.split("|", 1)
                    if len(parts) > 1:
                        desc = parts[1].strip()
                if name.lower() not in ("name", "название"):
                    out.append({"name": name, "type": ptype,
                                "required": required, "desc": desc[:400]})
        i += 1
    return out


def parse_scopes(text: str) -> list[str]:
    match = SCOPE_LINE.search(text)
    if not match:
        return []
    scopes: list[str] = []
    for bracketed, plain in TICKED.findall(match.group(1)):
        value = (bracketed or plain).strip()
        if not value or value == "...":
            continue
        for part in value.split(","):
            part = part.strip()
            if part and part not in SCOPE_NOISE:
                scopes.append(SCOPE_ALIAS.get(part, part))
    return scopes


def build(docs: Path) -> dict:
    catalog: dict[str, dict] = {}
    reference = docs / "api-reference"
    for md in reference.rglob("*.md"):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        title = next((line for line in text.splitlines() if H1.match(line)), None)
        if title is None:
            continue
        name = method_name(md, H1.match(title).group(1))
        if not name or "." not in name:
            continue
        entry = {
            "scope": parse_scopes(text),
            "deprecated": "DEPRECATED" in text[:4000].upper(),
            "doc": md.relative_to(docs).as_posix(),
            "params": parse_params(text),
        }
        # Prefer the page that actually documents parameters.
        existing = catalog.get(name)
        if existing and len(existing["params"]) >= len(entry["params"]):
            continue
        catalog[name] = entry
    return catalog


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the Bitrix24 REST method catalog")
    ap.add_argument("--docs", default=str(DEFAULT_CHECKOUT),
                    help="Path to a b24restdocs checkout (cloned here if absent).")
    args = ap.parse_args()

    docs = ensure_docs(Path(args.docs))
    catalog = build(docs)
    if len(catalog) < 1000:
        sys.exit(f"only {len(catalog)} methods parsed - the docs layout probably "
                 f"changed; refusing to overwrite a good catalog with a bad one.")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(catalog, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")), encoding="utf-8")

    with_params = sum(1 for v in catalog.values() if v["params"])
    print(f"\nmethods:            {len(catalog)}")
    print(f"  with parameters:  {with_params}")
    print(f"  deprecated:       {sum(1 for v in catalog.values() if v['deprecated'])}")
    print(f"  with scope:       {sum(1 for v in catalog.values() if v['scope'])}")
    print(f"written: {OUTPUT.relative_to(ROOT)} "
          f"({OUTPUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
