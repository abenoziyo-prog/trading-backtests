"""Parse `key=value` strategy parameters from the command line."""
from __future__ import annotations


def parse_value(raw: str):
    if raw.lower() in {"true", "false"}:
        return raw.lower() == "true"
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            pass
    return raw


def parse_params(pairs: list[str]) -> dict:
    params = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--param must look like key=value, got {pair!r}")
        key, raw = pair.split("=", 1)
        params[key] = parse_value(raw)
    return params
