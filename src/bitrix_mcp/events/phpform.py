"""Decode PHP-style bracket form encoding used by Bitrix24 outgoing webhooks.

Bitrix posts events as application/x-www-form-urlencoded with PHP array syntax:
    event=ONCRMDEALUPDATE
    data[FIELDS][ID]=662
    auth[application_token]=xxx
    auth[domain]=portal.tld

`parse_qsl` alone yields flat keys like "data[FIELDS][ID]" - this module folds
them back into nested dicts. Indexed keys ("data[0]", "data[1]") stay dicts
keyed by the literal index: Bitrix is not consistent about emitting contiguous
indices, and a dict never silently reorders or drops a gap.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl


def split_key(flat: str) -> list[str]:
    """'data[FIELDS][ID]' -> ['data', 'FIELDS', 'ID']; 'ts' -> ['ts']."""
    first = flat.find("[")
    if first == -1:
        return [flat]
    parts: list[str] = [flat[:first]]
    pos, n = first, len(flat)
    while pos < n:
        if flat[pos] != "[":
            break
        close = flat.find("]", pos)
        if close == -1:               # malformed - treat remainder as literal
            parts.append(flat[pos + 1:])
            return parts
        parts.append(flat[pos + 1:close])
        pos = close + 1
    return parts


def parse_php_form(body: str) -> dict[str, Any]:
    """Decode a urlencoded body with PHP bracket arrays into nested dicts."""
    out: dict[str, Any] = {}
    for flat, value in parse_qsl(body, keep_blank_values=True):
        parts = split_key(flat)
        node: Any = out
        for i, key in enumerate(parts):
            last = i == len(parts) - 1
            if key == "":             # PHP append syntax: a[]=1
                key = str(len([k for k in node if k.isdigit()])) if isinstance(node, dict) else "0"
            if last:
                node[key] = value
            else:
                nxt = node.get(key)
                if not isinstance(nxt, dict):
                    nxt = {}
                    node[key] = nxt
                node = nxt
    return out
