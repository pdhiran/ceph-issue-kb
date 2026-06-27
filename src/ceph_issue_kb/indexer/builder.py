"""Pipeline orchestrator: fetch -> normalize -> embed -> store.

Coordinates connectors, normalizer, and embedder to build the
knowledge base indices and write them to disk.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ceph_issue_kb.config import Config
from ceph_issue_kb.connectors import get_connector
from ceph_issue_kb.connectors.base import BaseConnector, ConnectorError
from ceph_issue_kb.indexer.normalizer import normalize
from ceph_issue_kb.models import NormalizedIssue

logger = logging.getLogger(__name__)


def build_index(
    config: Config,
    output_dir: str | Path,
    *,
    since: str | None = None,
    connectors_override: dict[str, BaseConnector] | None = None,
    full_rebuild: bool = False,
) -> dict[str, Any]:
    """Run the full indexing pipeline.

    1. For each enabled connector, fetch issues (since *since* or config default).
    2. Normalize every RawIssue into NormalizedIssue.
    3. Merge new issues with existing on-disk data (unless *full_rebuild*).
    4. Build merged BM25 index across all sources.
    5. Embed per source, build FAISS indices.
    6. Write everything to *output_dir*.

    *connectors_override* lets tests inject mock connectors.
    *full_rebuild* skips merging and overwrites all data from scratch.

    Returns a metadata dict summarising the build.
    """
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    per_source: dict[str, list[NormalizedIssue]] = {}
    stats: dict[str, Any] = {}

    for name, conn_config in config.enabled_connectors.items():
        if connectors_override and name in connectors_override:
            connector = connectors_override[name]
        else:
            try:
                connector = get_connector(conn_config)
            except (ConnectorError, Exception) as exc:
                logger.warning("Skipping connector %s: %s", name, exc)
                stats[name] = {"fetched": 0, "error": str(exc)}
                continue

        fetch_since = since or conn_config.since
        logger.info("Fetching from %s (since %s)...", name, fetch_since)

        raw_issues = []
        try:
            for raw in connector.fetch_updates(fetch_since):
                raw_issues.append(raw)
        except ConnectorError as exc:
            logger.warning("Error fetching from %s: %s", name, exc)
            stats[name] = {"fetched": 0, "error": str(exc)}
            continue

        normalized: list[NormalizedIssue] = []
        for raw in raw_issues:
            try:
                issue = normalize(raw)
                normalized.append(issue)
            except Exception as exc:
                logger.warning(
                    "Failed to normalize %s/%s: %s", name, raw.source_id, exc
                )

        per_source[name] = normalized
        stats[name] = {"fetched": len(raw_issues), "normalized": len(normalized)}
        logger.info(
            "%s: fetched %d, normalized %d", name, len(raw_issues), len(normalized)
        )

    _write_per_source(per_source, output, full_rebuild=full_rebuild)

    all_issues = _load_all_issues(per_source.keys(), output)

    _build_and_write_bm25(all_issues, output)

    _embed_per_source_from_disk(per_source.keys(), output)

    metadata = _write_metadata(stats, all_issues, output)

    return metadata


def _write_per_source(
    per_source: dict[str, list[NormalizedIssue]],
    output: Path,
    *,
    full_rebuild: bool = False,
) -> None:
    """Write issues.json per source directory, merging with existing data.

    Unless *full_rebuild* is True, existing issues are preserved and new
    issues are upserted by ``entity_id``.
    """
    from ceph_issue_kb.search.engine import _issue_to_dict

    for source_name, new_issues in per_source.items():
        source_dir = output / source_name
        source_dir.mkdir(parents=True, exist_ok=True)

        existing_file = source_dir / "issues.json"
        existing: dict[str, dict] = {}
        if not full_rebuild and existing_file.exists():
            data = json.loads(existing_file.read_text())
            existing = {issue["entity_id"]: issue for issue in data}

        for issue in new_issues:
            d = _issue_to_dict(issue)
            existing[d["entity_id"]] = d

        merged = list(existing.values())
        existing_file.write_text(json.dumps(merged, indent=2, default=str))
        logger.info(
            "Wrote %d issues to %s/issues.json (%d new/updated)",
            len(merged),
            source_name,
            len(new_issues),
        )


def _load_all_issues(
    source_names: Iterable[str], output: Path
) -> list[NormalizedIssue]:
    """Reload all merged issues from disk for the given sources."""
    from ceph_issue_kb.search.engine import _dict_to_issue

    all_issues: list[NormalizedIssue] = []
    for source_name in source_names:
        source_file = output / source_name / "issues.json"
        if source_file.exists():
            data = json.loads(source_file.read_text())
            all_issues.extend(_dict_to_issue(d) for d in data)
    return all_issues


def _build_and_write_bm25(issues: list[NormalizedIssue], output: Path) -> None:
    """Build a merged BM25 index metadata file."""

    bm25_data = {
        "doc_count": len(issues),
        "entity_ids": [issue.entity_id for issue in issues],
        "sources": list({issue.source for issue in issues}),
    }
    (output / "merged_bm25_index.json").write_text(
        json.dumps(bm25_data, indent=2)
    )
    logger.info("Wrote merged BM25 metadata for %d issues", len(issues))


def _embed_per_source_from_disk(
    source_names: Iterable[str], output: Path
) -> None:
    """Embed the full merged issue set per source and write FAISS indices."""
    from ceph_issue_kb.search.engine import _dict_to_issue

    try:
        from ceph_issue_kb.indexer.embedder import Embedder
    except ImportError:
        logger.warning("fastembed not available; skipping embedding step")
        return

    embedder = Embedder()
    for source_name in source_names:
        source_file = output / source_name / "issues.json"
        if not source_file.exists():
            continue
        data = json.loads(source_file.read_text())
        if not data:
            continue

        issues = [_dict_to_issue(d) for d in data]
        source_dir = output / source_name

        try:
            vectors, entity_ids = embedder.embed_issues(issues)
            index = embedder.build_faiss_index(vectors)

            import faiss  # type: ignore[import-untyped]

            faiss.write_index(index, str(source_dir / "faiss.index"))
            (source_dir / "faiss_ids.json").write_text(json.dumps(entity_ids))
            logger.info(
                "Wrote FAISS index for %s: %d vectors", source_name, len(entity_ids)
            )
        except ImportError:
            logger.warning("faiss-cpu not available; skipping FAISS for %s", source_name)
        except Exception as exc:
            logger.warning("Embedding failed for %s: %s", source_name, exc)


def _write_metadata(
    stats: dict[str, Any],
    all_issues: list[NormalizedIssue],
    output: Path,
) -> dict[str, Any]:
    """Write build metadata."""
    metadata = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "total_issues": len(all_issues),
        "sources": stats,
        "components": sorted(
            {c for issue in all_issues for c in issue.components}
        ),
    }
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2))
    logger.info("Build complete: %d total issues", len(all_issues))
    return metadata
