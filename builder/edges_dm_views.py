"""ViewSourceIndex: dm/views/*.sql → source FQNs (no TableNode, no FlowEdge)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Tuple

from .sql_refs import extract_sql_refs

log = logging.getLogger(__name__)

ViewSourceIndex = Dict[str, List[str]]
ViewIndexStats = Dict[str, int]


def build_view_source_index(export_root: Path) -> Tuple[ViewSourceIndex, ViewIndexStats]:
    """Scan export_root/dm/views/*.sql → view_fqn → [source_fqn, …].

    Does not create nodes or edges. Missing views dir → empty index.
    """
    views_dir = Path(export_root) / "dm" / "views"
    index: ViewSourceIndex = {}
    if not views_dir.is_dir():
        stats: ViewIndexStats = {"views": 0, "indexed": 0, "empty": 0}
        log.info("views=%s indexed=%s empty=%s", 0, 0, 0)
        return index, stats

    for path in sorted(views_dir.glob("*.sql")):
        stem = path.stem.lower()
        view_fqn = f"dm.{stem}"
        try:
            body = path.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("skip view %s: %s", path, e)
            index[view_fqn] = []
            continue
        index[view_fqn] = extract_sql_refs(body)

    views = len(index)
    indexed = sum(1 for refs in index.values() if refs)
    empty = views - indexed
    stats = {"views": views, "indexed": indexed, "empty": empty}
    log.info("views=%s indexed=%s empty=%s", views, indexed, empty)
    return index, stats
