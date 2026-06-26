#!/usr/bin/env python3
"""CLI to fetch, normalize, embed, and store Ceph issues.

Usage:
    python index_issues.py --config connectors.yaml --verbose
    python index_issues.py --connector ceph-tracker --verbose
    python index_issues.py --config connectors.yaml --since 2025-01-01 --verbose
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from ceph_issue_kb.config import load_config
from ceph_issue_kb.indexer.builder import build_index

DEFAULT_CONFIG = "connectors.yaml"
DEFAULT_OUTPUT_DIR = "knowledge/issues-2024-2025"

logger = logging.getLogger("ceph_issue_kb")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch, normalize, embed, and store Ceph issues.",
        epilog=(
            "Examples:\n"
            "  python index_issues.py --config connectors.yaml --verbose\n"
            "  python index_issues.py --connector ceph-tracker --verbose\n"
            "  python index_issues.py --since 2025-01-01 --verbose\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        metavar="PATH",
        help=f"Path to connectors.yaml (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--connector",
        metavar="NAME",
        help="Run a single connector instead of all enabled connectors",
    )
    parser.add_argument(
        "--since",
        metavar="DATE",
        help="Only fetch issues updated since this ISO date (e.g. 2025-01-01)",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        metavar="PATH",
        help=f"Output directory for the knowledge base (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    config_path = Path(args.config)
    try:
        config = load_config(config_path)
    except FileNotFoundError:
        logger.error("Config file not found: %s", config_path)
        return 1
    except ValueError as exc:
        logger.error("Invalid config: %s", exc)
        return 1

    if args.connector:
        if args.connector not in config.connectors:
            logger.error(
                "Connector %r not found in %s. Available: %s",
                args.connector,
                config_path,
                list(config.connectors.keys()),
            )
            return 1
        for name in list(config.connectors):
            if name != args.connector:
                config.connectors[name].enabled = False
            else:
                config.connectors[name].enabled = True

    enabled = config.enabled_connectors
    if not enabled:
        logger.error("No enabled connectors in %s", config_path)
        return 1

    logger.info(
        "Starting indexing: connectors=%s, output=%s, since=%s",
        list(enabled.keys()),
        args.output_dir,
        args.since or "(connector default)",
    )

    t0 = time.monotonic()
    try:
        metadata = build_index(config, args.output_dir, since=args.since)
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception as exc:
        logger.error("Indexing failed: %s", exc, exc_info=args.verbose)
        return 1

    elapsed = time.monotonic() - t0

    print()
    print(f"Indexing complete in {elapsed:.1f}s")
    print(f"  Total issues: {metadata.get('total_issues', 0)}")
    for source, info in metadata.get("sources", {}).items():
        fetched = info.get("fetched", 0)
        normalized = info.get("normalized", 0)
        error = info.get("error")
        if error:
            print(f"  {source}: ERROR — {error}")
        else:
            print(f"  {source}: {fetched} fetched, {normalized} normalized")
    print(f"  Output: {args.output_dir}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
