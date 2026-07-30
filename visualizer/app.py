"""Streamlit UI for entities lineage graph."""
from __future__ import annotations

import argparse
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

import streamlit as st
from pyvis.network import Network

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graph.lineage_graph import BRANCH_MAX_NODES  # noqa: E402
from visualizer.graph_view import (  # noqa: E402
    db_fingerprint,
    list_table_fqns_from_nodes,
    load_views,
    prepare_pyvis_data,
    status_banner_message,
)

# Background exact-recount job (Streamlit cannot process Stop mid-blocking loop).
_recount_lock = threading.Lock()
_recount_job = {
    "running": False,
    "stop": False,
    "idx": 0,
    "total": 0,
    "fqn": "",
    "scope": "",
    "message": None,
    "error": None,
}


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--schema-db", default=str(ROOT / "data" / "schema_cache.db"))
    p.add_argument("--counts-db", default=str(ROOT / "data" / "counts_cache.db"))
    # streamlit passes unknown args after --
    args, _ = p.parse_known_args()
    return args


@st.cache_resource
def _cached_topology(
    schema_path: str,
    counts_path: str,
    schema_fp: tuple,
    counts_fp: tuple,
):
    """Load topology once per DB fingerprint (mtime, size)."""
    _ = schema_fp, counts_fp  # part of cache key
    return load_views(Path(schema_path), Path(counts_path))


def _start_exact_recount(counts_db: Path, nodes, *, scope: str) -> Optional[str]:
    """Start background exact recount; returns error string or None if started."""
    from counter.count_service import (
        CountService,
        CountServiceError,
        RecountAborted,
    )
    from counter.counts_store import CountsStore
    from counter.gp_connector import GpConnector, GpConnectorError, default_ssh_factory
    from models import CountMode

    if not nodes:
        return "no visible tables for current filters"

    with _recount_lock:
        if _recount_job["running"]:
            return "recount already running"
        _recount_job.update(
            {
                "running": True,
                "stop": False,
                "idx": 0,
                "total": 0,
                "fqn": "",
                "scope": scope,
                "message": None,
                "error": None,
            }
        )

    # Snapshot for worker thread (UI may rerun).
    targets = list(nodes)
    counts_path = Path(counts_db)

    def worker() -> None:
        try:

            def should_stop() -> bool:
                with _recount_lock:
                    return bool(_recount_job["stop"])

            def on_progress(idx: int, total: int, fqn: str) -> None:
                with _recount_lock:
                    _recount_job["idx"] = idx
                    _recount_job["total"] = total
                    _recount_job["fqn"] = fqn

            connector = GpConnector(default_ssh_factory)
            with connector.session():
                service = CountService(connector, CountsStore(counts_path))
                updated = service.update_counts(
                    targets,
                    mode=CountMode.EXACT,
                    scope=scope,
                    on_progress=on_progress,
                    should_stop=should_stop,
                )
            zero = sum(1 for m in updated if m.row_count == 0)
            msg = (
                f"exact ok scope={scope} visible={len(targets)}: "
                f"updated={len(updated)} (zero={zero}, nonzero={len(updated) - zero})"
            )
            with _recount_lock:
                _recount_job["message"] = msg
                _recount_job["error"] = None
        except RecountAborted as e:
            zero = sum(1 for m in e.updated if m.row_count == 0)
            msg = (
                f"exact stopped scope={scope}: {e} "
                f"(zero={zero}, nonzero={len(e.updated) - zero})"
            )
            with _recount_lock:
                _recount_job["message"] = msg
                _recount_job["error"] = None
        except (CountServiceError, GpConnectorError) as e:
            detail = str(e)
            if isinstance(e, CountServiceError) and e.failures:
                detail += " | " + "; ".join(e.failures[:5])
            with _recount_lock:
                _recount_job["error"] = f"exact recount failed: {detail}"
                _recount_job["message"] = None
        except Exception as e:
            with _recount_lock:
                _recount_job["error"] = f"exact recount failed: {e}"
                _recount_job["message"] = None
        finally:
            try:
                _cached_topology.clear()
            except Exception:
                pass
            with _recount_lock:
                _recount_job["running"] = False

    threading.Thread(target=worker, daemon=True, name="exact-recount").start()
    return None


def _render_recount_progress() -> bool:
    """Show progress / result; return True if job still running (caller should rerun)."""
    with _recount_lock:
        running = bool(_recount_job["running"])
        idx = int(_recount_job["idx"])
        total = int(_recount_job["total"])
        fqn = str(_recount_job["fqn"] or "")
        scope = str(_recount_job["scope"] or "")
        message = _recount_job["message"]
        error = _recount_job["error"]

    if running:
        frac = (idx / total) if total else 0.0
        st.progress(frac, text=f"exact {scope}: {idx}/{total or '?'}")
        if fqn:
            st.info(f"Counting **`{fqn}`** ({idx}/{total})")
        else:
            st.info(f"Exact recount ({scope}): connecting…")
        return True

    if error:
        st.error(error)
        with _recount_lock:
            _recount_job["error"] = None
    elif message:
        st.info(message)
        with _recount_lock:
            _recount_job["message"] = None
    return False


def main():
    args = _parse_args()
    st.set_page_config(page_title="Entities Lineage", layout="wide")
    st.title("Entities Lineage")
    st.caption("Local property graph (SQLite + NetworkX + Pyvis), not Neo4j")

    schema_db = Path(st.sidebar.text_input("Schema DB", args.schema_db))
    counts_db = Path(st.sidebar.text_input("Counts DB", args.counts_db))
    src_code = st.sidebar.text_input("Filter src_code", "001")

    # Preload nodes for recount button (uses same cache as graph when possible).
    schema_fp = db_fingerprint(schema_db)
    counts_fp = db_fingerprint(counts_db)
    try:
        nodes, edges, views, lg = _cached_topology(
            str(schema_db),
            str(counts_db),
            schema_fp,
            counts_fp,
        )
    except Exception as e:
        st.error(status_banner_message(str(e)))
        return
    topology = (nodes, edges, views, lg)

    table_options = [""] + list_table_fqns_from_nodes(nodes, src_code=src_code or None)
    table_fqn = st.sidebar.selectbox("Table", table_options, index=0)
    branch_direction = st.sidebar.radio(
        "Branch direction",
        ["both", "upstream", "downstream"],
        index=0,
    )
    hops = st.sidebar.slider(
        f"Fallback hops (если ветка > {BRANCH_MAX_NODES})",
        1,
        5,
        2,
    )
    show_counts_on_label = st.sidebar.checkbox("Показать counts на label", False)
    group_by_layer = st.sidebar.checkbox("Group by layer", True)
    schema_bands = st.sidebar.multiselect(
        "Слой данных",
        ["stg_ods", "ods", "dds", "dm"],
        default=["stg_ods", "ods", "dds", "dm"],
    )

    try:
        payload = prepare_pyvis_data(
            schema_db,
            counts_db,
            src_code=src_code or None,
            table_fqn=table_fqn or None,
            branch_direction=branch_direction,
            hops=hops,
            show_counts_on_label=show_counts_on_label,
            group_by_layer=group_by_layer,
            schema_bands=schema_bands,
            topology=topology,
        )
    except Exception as e:
        st.error(status_banner_message(str(e)))
        return

    visible_fqns = set(payload.get("subgraph_fqns") or [])
    visible_nodes = [n for n in nodes if n.fqn in visible_fqns]

    with _recount_lock:
        recount_running = bool(_recount_job["running"])

    col1, col2, col3, col4 = st.columns(4)
    rebuild_msg = None
    with col1:
        if st.button("Обновить схему", disabled=recount_running):
            try:
                from cli import cmd_build_schema
                from argparse import Namespace

                export = ROOT.parent / "gp_metadata" / "gp_metadata_export"
                fixture = (
                    ROOT / "tests" / "with_fixtures" / "fixtures" / "meta_hm_houses.json"
                )
                ns = Namespace(
                    export_root=str(export),
                    schema_db=str(schema_db),
                    meta_fixture=str(fixture) if fixture.exists() else None,
                    live_meta=False,
                    stg_ods_only=False,
                    full=True,
                )
                code = cmd_build_schema(ns)
                _cached_topology.clear()
                rebuild_msg = "schema ok" if code == 0 else f"schema failed ({code})"
            except Exception as e:
                rebuild_msg = str(e)
    with col2:
        if st.button("Пересчитать пустые", disabled=recount_running):
            err = _start_exact_recount(counts_db, visible_nodes, scope="empty")
            if err:
                rebuild_msg = err
            else:
                st.rerun()
    with col3:
        if st.button("Пересчитать всё", disabled=recount_running):
            err = _start_exact_recount(counts_db, visible_nodes, scope="all")
            if err:
                rebuild_msg = err
            else:
                st.rerun()
    with col4:
        if st.button("Стоп", disabled=not recount_running, type="primary"):
            with _recount_lock:
                _recount_job["stop"] = True

    still_running = _render_recount_progress()
    if still_running:
        time.sleep(0.5)
        st.rerun()

    if rebuild_msg:
        banner = status_banner_message(
            rebuild_msg
            if "fail" in rebuild_msg.lower() or "error" in rebuild_msg.lower()
            else None
        )
        if banner:
            st.error(banner)
        else:
            st.info(rebuild_msg)
        # Reload after rebuild so graph sees new schema mtime.
        schema_fp = db_fingerprint(schema_db)
        counts_fp = db_fingerprint(counts_db)
        nodes, edges, views, lg = _cached_topology(
            str(schema_db),
            str(counts_db),
            schema_fp,
            counts_fp,
        )
        topology = (nodes, edges, views, lg)
        try:
            payload = prepare_pyvis_data(
                schema_db,
                counts_db,
                src_code=src_code or None,
                table_fqn=table_fqn or None,
                branch_direction=branch_direction,
                hops=hops,
                show_counts_on_label=show_counts_on_label,
                group_by_layer=group_by_layer,
                schema_bands=schema_bands,
                topology=topology,
            )
        except Exception as e:
            st.error(status_banner_message(str(e)))
            return

    # Refresh topology (counts may have changed after background recount).
    schema_fp = db_fingerprint(schema_db)
    counts_fp = db_fingerprint(counts_db)
    nodes, edges, views, lg = _cached_topology(
        str(schema_db),
        str(counts_db),
        schema_fp,
        counts_fp,
    )
    topology = (nodes, edges, views, lg)
    try:
        payload = prepare_pyvis_data(
            schema_db,
            counts_db,
            src_code=src_code or None,
            table_fqn=table_fqn or None,
            branch_direction=branch_direction,
            hops=hops,
            show_counts_on_label=show_counts_on_label,
            group_by_layer=group_by_layer,
            schema_bands=schema_bands,
            topology=topology,
        )
    except Exception as e:
        st.error(status_banner_message(str(e)))
        return

    if payload.get("error"):
        st.warning(payload["error"])
        return

    if payload.get("warning"):
        st.warning(payload["warning"])
    if payload.get("caption"):
        st.caption(payload["caption"])
    st.caption(f"Visible tables (filters): {len(payload.get('subgraph_fqns') or [])}")

    fqns = payload.get("subgraph_fqns") or []
    by_id = {n["id"]: n for n in payload["nodes"]}
    st.sidebar.markdown("### Node details")
    if fqns:
        selected = st.sidebar.selectbox("Selected node", fqns)
        node = by_id.get(selected, {})
        st.sidebar.write(f"**fqn:** {selected}")
        st.sidebar.write(f"**layer:** {node.get('layer', 'n/a')}")
        st.sidebar.write(f"**status:** {node.get('status', 'n/a')}")
        rc = node.get("row_count")
        st.sidebar.write(f"**row_count:** {rc if rc is not None else 'n/a'}")
        st.sidebar.write(f"**count_mode:** {node.get('count_mode') or 'n/a'}")
        if rc == 0 and node.get("count_mode") == "fast":
            st.sidebar.caption("note: reltuples may be stale")
        st.sidebar.write(f"**count_ts:** {node.get('count_ts') or 'n/a'}")
    else:
        st.sidebar.caption("Нет узлов в текущем subgraph")

    if payload.get("highlight") and not table_fqn:
        st.write("Upstream path:", " → ".join(payload["highlight"]))
    elif payload.get("highlight") and table_fqn:
        st.write("Selected table:", payload["highlight"][0])

    net = Network(height="700px", width="100%", directed=True)
    if group_by_layer:
        # Fixed-x bands; physics only along Y (no hierarchical — dm cycles).
        net.set_options(
            """
            {
              "layout": {"hierarchical": {"enabled": false}},
              "physics": {
                "enabled": true,
                "solver": "forceAtlas2Based",
                "forceAtlas2Based": {
                  "gravitationalConstant": -40,
                  "centralGravity": 0.005,
                  "springLength": 80,
                  "springConstant": 0.08,
                  "avoidOverlap": 0.4
                },
                "stabilization": {"iterations": 120}
              }
            }
            """
        )
    for n in payload["nodes"]:
        # Do NOT pass `group` to pyvis: Network.add_node drops `color` when
        # group is set (pyvis quirk). Payload still has group for tests/UI.
        kwargs = {
            "label": n["label"],
            "title": n["title"],
            "color": n["color"],
            "borderWidth": n.get("borderWidth", 1),
        }
        if "x" in n:
            kwargs["x"] = n["x"]
        if "fixed" in n:
            kwargs["fixed"] = n["fixed"]
        if "level" in n:
            kwargs["level"] = n["level"]
        net.add_node(n["id"], **kwargs)
    for e in payload["edges"]:
        net.add_edge(e["from"], e["to"])

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
        net.save_graph(tmp.name)
        html = Path(tmp.name).read_text(encoding="utf-8")
    st.components.v1.html(html, height=720, scrolling=True)


if __name__ == "__main__":
    main()
