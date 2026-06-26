# Agent Integration Guide — ceph-issue-kb

This guide covers integrating the Ceph Issue Intelligence KB with LLM agents, including IBM watsonx (Bob), LangChain, CrewAI, CI/CD pipelines, and custom agent frameworks.

## Architecture

```
┌─────────────────────┐       ┌──────────────────────┐
│    Agent / LLM      │       │   ceph-issue-kb      │
│  (Bob, LangChain,   │──────▶│   REST API           │
│   CrewAI, custom)   │ HTTP  │   :8200              │
└─────────────────────┘       └──────────────────────┘
         │                              │
         │ MCP (stdio)                  │ reads
         ▼                              ▼
┌─────────────────────┐       ┌──────────────────────┐
│   ceph-issue-kb     │       │   knowledge/         │
│   MCP Server        │       │   issues-2024-2025/  │
└─────────────────────┘       └──────────────────────┘
```

Two integration paths:
- **REST API** — for any HTTP client (agents, web apps, CI/CD scripts)
- **MCP** — for Cursor, Claude Desktop, and MCP-compatible tools

## Setup Instructions

### 1. Install the Ceph Issue KB

```bash
git clone https://github.com/pdhiran/ceph-issue-kb.git
cd ceph-issue-kb
pip install -e .
```

### 2. Fetch and Index Issues

```bash
# All active sources (requires credentials — see CREDENTIALS.md)
export JIRA_USERNAME=your_user JIRA_API_TOKEN=your_token
export RH_OFFLINE_TOKEN=your_token
python3 index_issues.py --config connectors.yaml --since 2024-01-01 --verbose

# Single source only
python3 index_issues.py --connector ibm-jira --verbose
```

### 3. Start the REST API Server

```bash
# Start on default port 8200
python3 -m ceph_issue_kb.server.rest_api

# Or specify custom host/port
python3 -m ceph_issue_kb.server.rest_api --host 0.0.0.0 --port 8200
```

### 4. Verify the Server is Running

```bash
curl http://localhost:8200/health
```

Expected response:
```json
{
  "status": "ok",
  "total_issues": 15700,
  "index_status": "loaded",
  "schema_version": "1.0",
  "kb_path": "knowledge/issues-2024-2025"
}
```

## REST API Reference

Base URL: `http://localhost:8200`

### Issue Search

```bash
curl -X POST http://localhost:8200/api/search_issues \
  -H "Content-Type: application/json" \
  -d '{"query": "OSD slow ops", "component": "rados", "limit": 5}'
```

Response:
```json
{
  "query": "OSD slow ops",
  "results": [
    {
      "entity_id": "22b7cddd3f1fc2b7",
      "entity_type": "issue",
      "source": "ibm-jira",
      "source_id": "IBMCEPH-12345",
      "source_url": "https://ibm-ceph.atlassian.net/browse/IBMCEPH-12345",
      "title": "OSD slow ops during recovery",
      "summary": "First ~500 chars of description...",
      "status": "open",
      "priority": "high",
      "components": ["rados"],
      "affected_versions": ["19.2.0"],
      "similarity": 0.87,
      "matched_signals": ["same component", "similar description"]
    }
  ]
}
```

### Check Known Issue

```bash
curl -X POST http://localhost:8200/api/is_known_issue \
  -H "Content-Type: application/json" \
  -d '{"error_message": "FAILED ceph_assert(googly > 0)", "version": "19.2.0"}'
```

### Find Workaround

```bash
curl -X POST http://localhost:8200/api/find_workaround \
  -H "Content-Type: application/json" \
  -d '{"query": "too many PGs per OSD"}'
```

### Search by Stacktrace

```bash
curl -X POST http://localhost:8200/api/search_stacktrace \
  -H "Content-Type: application/json" \
  -d '{"stacktrace": "Traceback (most recent call last):\n  File \"mgr/dashboard/...\"\n  ..."}'
```

### Search by Health Warning

```bash
curl -X POST http://localhost:8200/api/search_health_warning \
  -H "Content-Type: application/json" \
  -d '{"warning": "HEALTH_WARN too many PGs per OSD"}'
```

### Find Fix

```bash
curl -X POST http://localhost:8200/api/find_fix \
  -H "Content-Type: application/json" \
  -d '{"query": "dashboard module crash on startup"}'
```

### Component Health

```bash
curl http://localhost:8200/api/component_health/rgw
```

### Hot Issues

```bash
curl "http://localhost:8200/api/hot_issues?component=cephfs&limit=10"
```

### Health Check

```bash
curl http://localhost:8200/health
```

### Capabilities

```bash
curl http://localhost:8200/capabilities
```

## All REST Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/search_issues` | POST | Search issues across all sources |
| `/api/find_similar_issue` | POST | Find issues similar to a description |
| `/api/is_known_issue` | POST | Check if an error matches a known issue |
| `/api/find_workaround` | POST | Find known workarounds |
| `/api/find_fix` | POST | Find fixes, commits, PRs |
| `/api/find_related_issues` | POST | Get related/duplicate/linked issues |
| `/api/search_stacktrace` | POST | Find issues with similar stacktraces |
| `/api/search_health_warning` | POST | Find issues for a health warning |
| `/api/hot_issues` | GET | Most active recent issues |
| `/api/component_health/{component}` | GET | Open criticals, regressions, blockers |
| `/health` | GET | Server health + connector status |
| `/capabilities` | GET | Server capabilities and entity types |

## Python Client

See [`examples/agent_integration.py`](examples/agent_integration.py) for a ready-made client class.

```python
from examples.agent_integration import CephIssueKBClient

client = CephIssueKBClient("http://localhost:8200")

# Check if an error is known
result = client.is_known_issue("FAILED ceph_assert(googly > 0)", version="19.2.0")
if result["known"]:
    print(f"Known issue: {result['issue']['title']}")

# Search by health warning
issues = client.search_health_warning("HEALTH_WARN too many PGs per OSD")
for issue in issues:
    print(f"  [{issue['priority']}] {issue['title']} ({issue['source']})")

# Find workaround
workaround = client.find_workaround("too many PGs per OSD")
print(f"Workaround: {workaround['resolution']}")
```

## LangChain Integration

```python
from langchain.tools import Tool
from examples.agent_integration import CephIssueKBClient

client = CephIssueKBClient()

search_issues_tool = Tool(
    name="search_ceph_issues",
    description="Search for known Ceph issues by keyword, error message, or component.",
    func=lambda query: client.search_issues(query, limit=5),
)

check_known_issue_tool = Tool(
    name="check_known_ceph_issue",
    description="Check if a Ceph error message matches a known issue.",
    func=lambda error: client.is_known_issue(error),
)

find_workaround_tool = Tool(
    name="find_ceph_workaround",
    description="Find workarounds for a known Ceph issue.",
    func=lambda query: client.find_workaround(query),
)
```

## CrewAI Integration

```python
from crewai import Agent, Task
from crewai_tools import tool
from examples.agent_integration import CephIssueKBClient

client = CephIssueKBClient()

@tool("Search Ceph Issues")
def search_ceph_issues(query: str, component: str = "") -> str:
    """Search for known Ceph issues. Component can be: rados, rbd, rgw, cephfs, cephadm."""
    results = client.search_issues(query, component=component or None, limit=5)
    return "\n\n".join(f"[{r['priority']}] {r['title']}\n{r['summary'][:200]}..." for r in results)

@tool("Check Known Ceph Issue")
def check_known_issue(error_message: str) -> str:
    """Check if a Ceph error message matches a known issue."""
    result = client.is_known_issue(error_message)
    if result["known"]:
        return f"Known issue: {result['issue']['title']} ({result['issue']['source_url']})"
    return "No matching known issue found."

ceph_triage_agent = Agent(
    role="Ceph Issue Triage Expert",
    goal="Identify known Ceph issues and find workarounds",
    tools=[search_ceph_issues, check_known_issue],
)
```

## Combined Workflow (All Three KBs)

For maximum accuracy, use all three KBs together:

```python
from examples.agent_integration import CephIssueKBClient
import requests

issue_client = CephIssueKBClient("http://localhost:8200")
doc_api = "http://localhost:8100"
cmd_api = "http://localhost:9090"

def triage_ceph_error(error_message: str, version: str = "") -> dict:
    """Full triage: check known issues -> find workaround -> verify fix -> find docs."""

    # Step 1: Is this a known issue?
    known = issue_client.is_known_issue(error_message, version=version)

    # Step 2: Find workaround if known
    workaround = None
    if known.get("known"):
        workaround = issue_client.find_workaround(known["issue"]["entity_id"])

    # Step 3: Verify any commands in the workaround with cmd-kb
    verified_commands = {}
    if workaround and workaround.get("commands"):
        for cmd in workaround["commands"]:
            resp = requests.post(f"{cmd_api}/api/verify_command", json={"command": cmd})
            verified_commands[cmd] = resp.json()

    # Step 4: Find relevant documentation with doc-kb
    docs = requests.get(f"{doc_api}/api/search", params={
        "query": error_message[:100], "limit": 3
    }).json()

    return {
        "known_issue": known,
        "workaround": workaround,
        "verified_commands": verified_commands,
        "documentation": docs.get("results", []),
    }
```

## Deployment

### systemd Service

Create `/etc/systemd/system/ceph-issue-kb.service`:

```ini
[Unit]
Description=Ceph Issue KB REST API
After=network.target

[Service]
Type=simple
User=ceph-kb
WorkingDirectory=/opt/ceph-issue-kb
ExecStart=/opt/ceph-issue-kb/.venv/bin/python3 -m ceph_issue_kb.server.rest_api --host 0.0.0.0 --port 8200
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable ceph-issue-kb
sudo systemctl start ceph-issue-kb
sudo systemctl status ceph-issue-kb
```

### Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install -e .
EXPOSE 8200
CMD ["python3", "-m", "ceph_issue_kb.server.rest_api", "--host", "0.0.0.0"]
```

```bash
docker build -t ceph-issue-kb .
docker run -d -p 8200:8200 --name ceph-issue-kb \
  -e JIRA_USERNAME=... -e JIRA_API_TOKEN=... \
  ceph-issue-kb
```

### Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ceph-issue-kb
spec:
  replicas: 2
  selector:
    matchLabels:
      app: ceph-issue-kb
  template:
    metadata:
      labels:
        app: ceph-issue-kb
    spec:
      containers:
      - name: api
        image: ceph-issue-kb:latest
        ports:
        - containerPort: 8200
        envFrom:
        - secretRef:
            name: ceph-issue-kb-credentials
        livenessProbe:
          httpGet:
            path: /health
            port: 8200
          initialDelaySeconds: 15
          periodSeconds: 30
---
apiVersion: v1
kind: Service
metadata:
  name: ceph-issue-kb
spec:
  selector:
    app: ceph-issue-kb
  ports:
  - port: 8200
    targetPort: 8200
  type: ClusterIP
```

## Agent Prompt Best Practices

When integrating with an LLM agent, include these instructions in the system prompt:

```
You have access to the Ceph Issue Intelligence KB. Use it as follows:

1. For crash/error reports → is_known_issue first (fastest path to "this is a known bug")
2. For HEALTH_WARN/ERR alerts → search_health_warning with the warning code
3. For stacktraces → search_stacktrace to find matching failure patterns
4. For workarounds → find_workaround once a matching issue is found
5. For fixes → find_fix to find commits, PRs, or backports
6. Always cross-reference with ceph-cmd-kb to verify workaround commands
7. Always cross-reference with ceph-doc-kb for relevant documentation
```

## Best Practices

1. **Check known issues first** — `is_known_issue` is the fastest triage path
2. **Use signal-specific search** — prefer `search_stacktrace` or `search_health_warning` over generic `search_issues` when you have structured signals
3. **Cross-reference workarounds** — verify commands from workarounds with ceph-cmd-kb before recommending
4. **Monitor component health** — use `component_health` for proactive risk assessment
5. **Handle multiple sources** — issues may appear in multiple trackers; use `find_related_issues` to deduplicate
6. **Use appropriate timeouts** — recommended: 30 seconds for search, 10 seconds for health checks
7. **Cache results** when possible to reduce API calls

## Troubleshooting

### Server won't start
- Check if port 8200 is already in use: `lsof -i :8200`
- Verify dependencies: `pip list | grep -E "pyyaml|mcp|uvicorn|starlette"`
- Check knowledge base exists: `ls knowledge/issues-2024-2025/`

### No issues found
- Verify connectors are configured: `cat connectors.yaml`
- Run indexer: `python3 index_issues.py --connector ibm-jira --verbose`
- Check connector health: `curl http://localhost:8200/health`

### Slow responses
- First request loads the index into memory (may take 5-10 seconds for large indices)
- Subsequent requests are fast (< 200ms for BM25, < 500ms for semantic)
- Consider pre-warming the cache on startup

## Security Considerations

The REST API serves indexed issue data that may include confidential content from IBM JIRA and Red Hat Bugzilla. Keep the following in mind:

- **Default bind address**: The REST API should default to `127.0.0.1`, not `0.0.0.0`. Only bind to `0.0.0.0` when behind a reverse proxy or in a trusted network.
- **Authentication**: Production deployments should add authentication in front of the API — an API key, a reverse proxy with auth (e.g., nginx + OAuth), or mTLS.
- **Credential isolation**: Do not pass indexer credentials (`JIRA_USERNAME`, `JIRA_API_TOKEN`, `RH_OFFLINE_TOKEN`) into the serving container. The serving phase only needs the pre-built `knowledge/` directory, not access to issue trackers.

## Support

- GitHub Issues: https://github.com/pdhiran/ceph-issue-kb/issues
- Documentation: See [DEVELOPMENT.md](DEVELOPMENT.md) for architecture details
- Platform Contract: See [SPEC.md](SPEC.md) for MCP contract
