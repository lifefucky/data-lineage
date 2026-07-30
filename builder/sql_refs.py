"""Extract schema.ident FQNs from SQL bodies (views/functions). Offline only."""
from __future__ import annotations

import re
from typing import List, Set

_IGNORE_SCHEMAS = frozenset(
    {
        "pg_catalog",
        "information_schema",
        "dm_service",
    }
)

# Strip -- line comments and /* block comments */ before matching.
_COMMENT_RE = re.compile(
    r"(--[^\n]*|/\*.*?\*/)",
    re.DOTALL,
)

# Read: FROM|JOIN + schema.ident (exclude DELETE FROM — that is a write).
_READ_REF_RE = re.compile(
    r"""(?ix)
    (?:
        (?<!\bDELETE\s)\bFROM\b
      | \bJOIN\b
    )
    \s+
    (?P<schema>"?[A-Za-z_][A-Za-z0-9_]*"?)
    \s*\.\s*
    (?P<name>"?[A-Za-z_][A-Za-z0-9_]*"?)
    """,
)

# Write: UPDATE|INTO|DELETE FROM + schema.ident
_WRITE_REF_RE = re.compile(
    r"""(?ix)
    (?:
        \bUPDATE\b
      | \bINTO\b
      | \bDELETE\s+FROM\b
    )
    \s+
    (?P<schema>"?[A-Za-z_][A-Za-z0-9_]*"?)
    \s*\.\s*
    (?P<name>"?[A-Za-z_][A-Za-z0-9_]*"?)
    """,
)

# Union (views / general): FROM|JOIN|UPDATE|INTO|DELETE FROM
_REF_RE = re.compile(
    r"""(?ix)
    (?:
        \bFROM\b
      | \bJOIN\b
      | \bUPDATE\b
      | \bINTO\b
      | \bDELETE\s+FROM\b
    )
    \s+
    (?P<schema>"?[A-Za-z_][A-Za-z0-9_]*"?)
    \s*\.\s*
    (?P<name>"?[A-Za-z_][A-Za-z0-9_]*"?)
    """,
)


def _strip_comments(sql: str) -> str:
    return _COMMENT_RE.sub(" ", sql)


def _unquote(ident: str) -> str:
    ident = ident.strip()
    if len(ident) >= 2 and ident[0] == '"' and ident[-1] == '"':
        return ident[1:-1]
    return ident


def _is_noise(schema: str, name: str) -> bool:
    if schema in _IGNORE_SCHEMAS:
        return True
    if name.startswith("bfr_"):
        return True
    return False


def _extract_with(pattern: re.Pattern[str], sql: str) -> List[str]:
    cleaned = _strip_comments(sql)
    seen: Set[str] = set()
    out: List[str] = []
    for m in pattern.finditer(cleaned):
        schema = _unquote(m.group("schema")).lower()
        name = _unquote(m.group("name")).lower()
        if _is_noise(schema, name):
            continue
        fqn = f"{schema}.{name}"
        if fqn in seen:
            continue
        seen.add(fqn)
        out.append(fqn)
    return out


def extract_read_refs(sql: str) -> List[str]:
    """Unique schema.ident from FROM/JOIN (first-seen order, lower-case)."""
    return _extract_with(_READ_REF_RE, sql)


def extract_write_refs(sql: str) -> List[str]:
    """Unique schema.ident from UPDATE/INTO/DELETE FROM (first-seen order)."""
    return _extract_with(_WRITE_REF_RE, sql)


def extract_sql_refs(sql: str) -> List[str]:
    """Unique schema.ident FQNs in first-seen order (lower-case). Union read+write."""
    return _extract_with(_REF_RE, sql)
