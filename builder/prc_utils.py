"""Shared helpers for dm procedure / mart naming (Airflow template parity)."""
from __future__ import annotations

# Order matters: longer prefixes first (same as template replace chain).
_PREFIXES = ("func_reload_", "func_load_", "func_calc_", "func_build_", "func_")


def mart_name_from_prc(prc_code: str) -> str:
    """Strip func_{reload,load,calc,build,_} prefix → mart table name."""
    code = prc_code.strip()
    for prefix in _PREFIXES:
        if code.startswith(prefix):
            return code[len(prefix) :].lower()
    return code.lower()
