"""Read-only live probe: gp_* cache vs parent/partition/exact (Phase 2).

Usage (from entities_lineage/):
  python tools/probe_gp_counts.py
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from counter.gp_connector import GpConnector, default_ssh_factory  # noqa: E402
from counter.logging_setup import configure_live_logging  # noqa: E402

DATA = ROOT / "data"
INVENTORY = DATA / "probe_gp_phase0_inventory.json"
OUT = DATA / "probe_gp_phase2_live.json"

# Skip full count(*) when cache suggests a heavy scan.
EXACT_MAX_CACHE_HINT = 100_000
# stale_fast: |cache-D|/max(D,1) > 0.20 and abs > 100
STALE_REL = 0.20
STALE_ABS = 100


def _safe(name: str) -> bool:
    return bool(name) and all(c.isalnum() or c == "_" for c in name)


def split_fqn(fqn: str) -> tuple[str, str]:
    schema, name = fqn.split(".", 1)
    return schema, name


def probe_one(conn: GpConnector, fqn: str, cache: dict) -> dict:
    schema, name = split_fqn(fqn)
    if not _safe(schema) or not _safe(name):
        return {"fqn": fqn, "error": "unsafe ident"}

    cache_rc = cache.get("row_count")
    cache_mode = cache.get("count_mode")

    a = conn.fetch_one(
        """
        SELECT c.relkind,
               CASE WHEN coalesce(c.reltuples, 0) < 0 THEN 0
                    ELSE coalesce(c.reltuples, 0)::bigint END AS reltuples,
               c.relpages
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = %s AND c.relname = %s
        """,
        (schema, name),
    )
    parent_rt = int(a["reltuples"]) if a else None
    relkind = a["relkind"] if a else None
    relpages = int(a["relpages"]) if a else None

    leaves = conn.fetch_all(
        """
        SELECT child.relname AS partition_name,
               CASE WHEN coalesce(child.reltuples, 0) < 0 THEN 0
                    ELSE coalesce(child.reltuples, 0)::bigint END AS reltuples,
               child.relpages
          FROM pg_inherits i
          JOIN pg_class parent ON i.inhparent = parent.oid
          JOIN pg_namespace pn ON pn.oid = parent.relnamespace
          JOIN pg_class child ON i.inhrelid = child.oid
         WHERE pn.nspname = %s AND parent.relname = %s
         ORDER BY 1
        """,
        (schema, name),
    )
    part_sum = sum(int(r["reltuples"]) for r in leaves) if leaves else 0
    n_part = len(leaves)

    # one more level (H2): grandchildren of first-level children
    deep_sum = 0
    deep_n = 0
    if leaves:
        for leaf in leaves:
            pname = str(leaf["partition_name"])
            if not _safe(pname):
                continue
            kids = conn.fetch_all(
                """
                SELECT CASE WHEN coalesce(child.reltuples, 0) < 0 THEN 0
                            ELSE coalesce(child.reltuples, 0)::bigint END AS reltuples
                  FROM pg_inherits i
                  JOIN pg_class parent ON i.inhparent = parent.oid
                  JOIN pg_namespace pn ON pn.oid = parent.relnamespace
                  JOIN pg_class child ON i.inhrelid = child.oid
                 WHERE pn.nspname = %s AND parent.relname = %s
                """,
                (schema, pname),
            )
            if kids:
                deep_n += len(kids)
                deep_sum += sum(int(r["reltuples"]) for r in kids)

    cstat = conn.fetch_one(
        """
        SELECT n_live_tup, n_dead_tup, last_analyze, last_autoanalyze
          FROM pg_stat_all_tables
         WHERE schemaname = %s AND relname = %s
        """,
        (schema, name),
    )

    skip_exact = (
        cache_rc is not None
        and int(cache_rc) > EXACT_MAX_CACHE_HINT
    )
    exact = None
    exact_error = None
    if skip_exact:
        exact_error = f"skipped count(*) cache_hint>{EXACT_MAX_CACHE_HINT}"
    else:
        try:
            row = conn.fetch_one(
                f'SELECT count(*) AS cnt FROM "{schema}"."{name}"'
            )
            exact = int(row["cnt"]) if row else None
        except Exception as e:
            exact_error = str(e)

    classification = classify(
        cache_rc=cache_rc,
        cache_mode=cache_mode,
        parent_rt=parent_rt,
        part_sum=part_sum,
        exact=exact,
        skip_exact=skip_exact,
    )

    return {
        "fqn": fqn,
        "cache_row_count": cache_rc,
        "cache_count_mode": cache_mode,
        "A_parent_reltuples": parent_rt,
        "A_relkind": relkind,
        "A_relpages": relpages,
        "B_n_partitions": n_part,
        "B_sum_reltuples": part_sum,
        "B2_deep_n": deep_n,
        "B2_deep_sum_reltuples": deep_sum,
        "C_n_live_tup": int(cstat["n_live_tup"]) if cstat and cstat["n_live_tup"] is not None else None,
        "C_last_analyze": str(cstat["last_analyze"]) if cstat else None,
        "C_last_autoanalyze": str(cstat["last_autoanalyze"]) if cstat else None,
        "D_exact": exact,
        "D_error": exact_error,
        "classification": classification,
        "leaves_sample": [
            {"name": r["partition_name"], "reltuples": int(r["reltuples"])}
            for r in leaves[:5]
        ],
    }


def classify(
    *,
    cache_rc,
    cache_mode,
    parent_rt,
    part_sum,
    exact,
    skip_exact,
) -> str:
    if cache_rc is None:
        return "missing_cache"
    cache_rc = int(cache_rc)
    catalog_best = max(
        parent_rt if parent_rt is not None else 0,
        part_sum or 0,
    )

    if exact is not None:
        if cache_rc == 0 and exact > 0:
            return "false_zero"
        if cache_rc > 0 and exact == 0:
            return "false_nonzero"
        abs_err = abs(cache_rc - exact)
        rel = abs_err / max(exact, 1)
        if abs_err > STALE_ABS and rel > STALE_REL:
            if cache_mode == "fast":
                return "stale_fast"
            return "stale_exact"
        return "ok"

    if skip_exact:
        # compare cache to catalog only
        if cache_rc == 0 and catalog_best > 0:
            return "suspect_false_zero_no_D"
        if cache_rc > 0 and catalog_best == 0:
            return "suspect_false_nonzero_no_D"
        abs_err = abs(cache_rc - catalog_best)
        rel = abs_err / max(catalog_best, 1)
        if catalog_best > 0 and abs_err > STALE_ABS and rel > STALE_REL:
            return "cache_vs_catalog_diverge"
        if cache_rc == catalog_best or (
            cache_mode == "exact" and abs_err <= STALE_ABS
        ):
            return "ok_catalog_no_D"
        return "cache_lag_or_stale_no_D"

    return "no_exact"


def build_sample(inv: dict) -> list[str]:
    """All zeros + known + up to 15 nonzero with preference for D-able sizes."""
    known = "stg_ods.gp_020_nsi_account_operation_types"
    zeros = inv.get("zero_exact_sample") or []
    # full zero list from inventory file if present
    zero_total = inv.get("zero_exact_total", 0)
    # re-read zeros from phase0: only sample listed; expand via summary path
    fqns = [known]
    # Prefer full zero list: reload from phase0 if we stored only sample —
    # phase0 wrote zero_exact_sample (30) and phase2_sample_fqns (all zeros).
    for f in inv.get("phase2_sample_fqns") or []:
        if f not in fqns:
            fqns.append(f)
    # Cap nonzero heavy: keep zeros + known + nonzero where cache <= EXACT_MAX
    # or a few heavy for A/B only.
    return fqns


def load_cache_map() -> dict:
    import sqlite3

    con = sqlite3.connect(str(DATA / "counts_cache.db"))
    con.row_factory = sqlite3.Row
    return {
        r["fqn"]: {
            "row_count": r["row_count"],
            "count_mode": r["count_mode"],
            "count_ts": r["count_ts"],
        }
        for r in con.execute("SELECT * FROM table_metrics")
    }


def main() -> int:
    configure_live_logging()
    logging.getLogger(__name__).setLevel(logging.INFO)
    inv = json.loads(INVENTORY.read_text(encoding="utf-8"))
    sample = build_sample(inv)
    cache_map = load_cache_map()

    import sqlite3

    sc = sqlite3.connect(str(DATA / "schema_cache.db"))
    gp_set = {
        r[0] for r in sc.execute("SELECT fqn FROM nodes WHERE layer = 'gp'")
    }
    zero_gp = sorted(
        fqn
        for fqn, m in cache_map.items()
        if fqn in gp_set and int(m["row_count"]) == 0
    )
    nz = sorted(
        (
            {
                "fqn": fqn,
                "row_count": int(m["row_count"]),
                "count_mode": m["count_mode"],
            }
            for fqn, m in cache_map.items()
            if fqn in gp_set and int(m["row_count"]) > 0
        ),
        key=lambda r: r["row_count"],
        reverse=True,
    )
    small = [r for r in nz if r["row_count"] <= EXACT_MAX_CACHE_HINT]
    large = [r for r in nz if r["row_count"] > EXACT_MAX_CACHE_HINT]
    pick_small = small[:5] + small[-5:] if len(small) >= 10 else small
    seen: set = set()
    pick_s = []
    for r in pick_small:
        if r["fqn"] not in seen:
            seen.add(r["fqn"])
            pick_s.append(r)
    pick_s = pick_s[:12]
    pick_l = large[:3]

    sample = []
    for f in ["stg_ods.gp_020_nsi_account_operation_types"] + zero_gp:
        if f not in sample:
            sample.append(f)
    for r in pick_s + pick_l:
        if r["fqn"] not in sample:
            sample.append(r["fqn"])

    print(f"probe sample n={len(sample)} zeros={len(zero_gp)} "
          f"small_nz={len(pick_s)} large_nz={len(pick_l)}")

    results = []
    connector = GpConnector(default_ssh_factory)
    with connector.session():
        for i, fqn in enumerate(sample, 1):
            print(f"[{i}/{len(sample)}] {fqn}", flush=True)
            rec = probe_one(connector, fqn, cache_map.get(fqn, {}))
            results.append(rec)
            print(f"  -> {rec['classification']}", flush=True)

    by_cls: dict = {}
    for r in results:
        by_cls.setdefault(r["classification"], []).append(r["fqn"])

    artifact = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "thresholds": {
            "EXACT_MAX_CACHE_HINT": EXACT_MAX_CACHE_HINT,
            "STALE_REL": STALE_REL,
            "STALE_ABS": STALE_ABS,
        },
        "sample_n": len(results),
        "classification_counts": {k: len(v) for k, v in sorted(by_cls.items())},
        "classification_fqns": {k: v[:30] for k, v in sorted(by_cls.items())},
        "results": results,
    }
    OUT.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    print("wrote", OUT)
    print("classification_counts", artifact["classification_counts"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
