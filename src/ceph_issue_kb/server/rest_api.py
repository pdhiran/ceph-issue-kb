"""REST API for the Ceph Issue Intelligence Knowledge Base.

Mirrors every MCP tool as an HTTP endpoint so non-MCP agents (watsonx,
LangChain, CI pipelines) can query the knowledge base over HTTP.

Run with::

    python -m ceph_issue_kb.server.rest_api
    python -m ceph_issue_kb.server.rest_api --host 0.0.0.0 --port 9000
    python -m ceph_issue_kb.server.rest_api --kb-path /data/kb

Default bind: 127.0.0.1:8200
Note: For production deployments, add authentication middleware.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ceph_issue_kb.server.kb import KnowledgeBase

logger = logging.getLogger(__name__)


def _error_response(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"error": message, "status": "error"}, status_code=status_code)


async def _parse_json(request: Request) -> dict | None:
    """Parse JSON body, returning None on decode failure."""
    try:
        return await request.json()
    except Exception:
        return None


def create_app(kb: KnowledgeBase) -> Starlette:
    """Build a Starlette application wired to *kb*."""

    async def post_search_issues(request: Request) -> JSONResponse:
        body = await _parse_json(request)
        if body is None:
            return _error_response("Invalid JSON body")
        query = body.get("query")
        if not query:
            return _error_response("'query' is required")
        return JSONResponse(kb.search_issues(
            query,
            source=body.get("source"),
            component=body.get("component"),
            version=body.get("version"),
            status=body.get("status"),
            limit=body.get("limit", 10),
        ))

    async def post_find_similar(request: Request) -> JSONResponse:
        body = await _parse_json(request)
        if body is None:
            return _error_response("Invalid JSON body")
        description = body.get("description")
        if not description:
            return _error_response("'description' is required")
        return JSONResponse(kb.find_similar_issue(
            description,
            stacktrace=body.get("stacktrace"),
            component=body.get("component"),
        ))

    async def post_is_known_issue(request: Request) -> JSONResponse:
        body = await _parse_json(request)
        if body is None:
            return _error_response("Invalid JSON body")
        error_message = body.get("error_message")
        if not error_message:
            return _error_response("'error_message' is required")
        return JSONResponse(kb.is_known_issue(
            error_message, version=body.get("version"),
        ))

    async def post_find_workaround(request: Request) -> JSONResponse:
        body = await _parse_json(request)
        if body is None:
            return _error_response("Invalid JSON body")
        query = body.get("query")
        if not query:
            return _error_response("'query' is required")
        return JSONResponse(kb.find_workaround(query))

    async def post_find_fix(request: Request) -> JSONResponse:
        body = await _parse_json(request)
        if body is None:
            return _error_response("Invalid JSON body")
        query = body.get("query")
        if not query:
            return _error_response("'query' is required")
        return JSONResponse(kb.find_fix(query))

    async def post_find_related(request: Request) -> JSONResponse:
        body = await _parse_json(request)
        if body is None:
            return _error_response("Invalid JSON body")
        issue_id = body.get("issue_id")
        if not issue_id:
            return _error_response("'issue_id' is required")
        return JSONResponse(kb.find_related_issues(issue_id))

    async def post_search_stacktrace(request: Request) -> JSONResponse:
        body = await _parse_json(request)
        if body is None:
            return _error_response("Invalid JSON body")
        stacktrace = body.get("stacktrace")
        if not stacktrace:
            return _error_response("'stacktrace' is required")
        return JSONResponse(kb.search_stacktrace(stacktrace))

    async def post_search_health_warning(request: Request) -> JSONResponse:
        body = await _parse_json(request)
        if body is None:
            return _error_response("Invalid JSON body")
        warning = body.get("warning")
        if not warning:
            return _error_response("'warning' is required")
        return JSONResponse(kb.search_health_warning(warning))

    async def get_hot_issues(request: Request) -> JSONResponse:
        component = request.query_params.get("component")
        limit = int(request.query_params.get("limit", "10"))
        return JSONResponse(kb.hot_issues(component=component, limit=limit))

    async def get_component_health(request: Request) -> JSONResponse:
        component = request.path_params["component"]
        return JSONResponse(kb.component_health(component))

    async def get_health(request: Request) -> JSONResponse:
        return JSONResponse(kb.health())

    async def get_capabilities(request: Request) -> JSONResponse:
        return JSONResponse(kb.capabilities())

    routes = [
        Route("/api/search_issues", post_search_issues, methods=["POST"]),
        Route("/api/find_similar_issue", post_find_similar, methods=["POST"]),
        Route("/api/is_known_issue", post_is_known_issue, methods=["POST"]),
        Route("/api/find_workaround", post_find_workaround, methods=["POST"]),
        Route("/api/find_fix", post_find_fix, methods=["POST"]),
        Route("/api/find_related_issues", post_find_related, methods=["POST"]),
        Route("/api/search_stacktrace", post_search_stacktrace, methods=["POST"]),
        Route("/api/search_health_warning", post_search_health_warning, methods=["POST"]),
        Route("/api/hot_issues", get_hot_issues, methods=["GET"]),
        Route("/api/component_health/{component}", get_component_health, methods=["GET"]),
        Route("/health", get_health, methods=["GET"]),
        Route("/capabilities", get_capabilities, methods=["GET"]),
    ]

    return Starlette(routes=routes)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ceph Issue KB REST API")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8200, help="Bind port (default: 8200)")
    parser.add_argument("--kb-path", default=None, help="Knowledge base directory")
    parser.add_argument(
        "--auto-update",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Auto-pull latest knowledge base from git on startup (default: enabled)",
    )
    parser.add_argument(
        "--update-interval",
        type=float,
        default=12,
        metavar="HOURS",
        help="Hours between periodic KB update checks (default: 12, 0=disable periodic)",
    )
    args = parser.parse_args(argv)

    from ceph_issue_kb.server.mcp_server import _find_kb_path

    kb_dir = _find_kb_path(args.kb_path)
    if kb_dir:
        logger.info("Loading knowledge base from %s", kb_dir)
        kb = KnowledgeBase.load(kb_dir)
    else:
        logger.warning("No knowledge base found — API will report degraded health")
        kb = KnowledgeBase.empty()

    if args.auto_update:
        from ceph_issue_kb.server.auto_update import start_auto_update
        start_auto_update(
            kb, kb_dir,
            update_interval_hours=args.update_interval,
        )

    app = create_app(kb)

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
