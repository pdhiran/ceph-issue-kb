# Credential Setup Guide

This guide walks you through generating and configuring credentials for the active issue sources.

## Overview

| Source | Auth Method | Credentials Needed | Env Variables |
|--------|------------|-------------------|---------------|
| IBM Ceph JIRA | API Token | Email + token | `JIRA_USERNAME`, `JIRA_API_TOKEN` |
| Red Hat KB | Offline Token | API token | `RH_OFFLINE_TOKEN` |

## Step 1: Create a `.env` file

```bash
cd /path/to/ceph-issue-kb
touch .env
```

This file is gitignored and will never be committed. Add credentials here as you generate them.

---

## IBM Ceph JIRA (Atlassian)

### Generate API Token

1. Go to [Atlassian API Tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Log in with your IBM Atlassian account
3. Click **"Create API token"** (not "Create API token with scopes")
4. Enter a label: `ceph-issue-kb`
5. Click **Create**
6. **Copy the token immediately** — it will not be shown again

### Add to `.env`

```bash
JIRA_USERNAME=your.name@ibm.com
JIRA_API_TOKEN=ATATT3xFfGF0...your_token_here
```

### Verify

```bash
source .env && export JIRA_USERNAME JIRA_API_TOKEN
python3 -c "
from ceph_issue_kb.config import load_config
from ceph_issue_kb.connectors import get_connector
config = load_config('connectors.yaml')
conn = get_connector(config.connectors['ibm-jira'])
conn.authenticate()
print(conn.health())
"
```

### Notes
- Token never expires by default, but can be revoked at any time
- Uses Basic auth: `Authorization: Basic base64(email:token)`
- Requires access to the IBMCEPH project on the Atlassian instance

---

## Red Hat Knowledge Base

### Generate Offline API Token

The RH KB connector uses an **offline API token** that doesn't expire — much more reliable than browser cookies.

1. Go to [Red Hat API Tokens](https://access.redhat.com/management/api)
2. Log in with your Red Hat account
3. Click **"Generate Token"**
4. Copy the offline token (long string starting with `eyJ...`)

### Add to `.env`

```bash
RH_OFFLINE_TOKEN=eyJhbGciOiJSUzI1NiIsInR5cCI...your_token_here
```

### Verify

```bash
source .env && export RH_OFFLINE_TOKEN
python3 -c "
from ceph_issue_kb.config import load_config
from ceph_issue_kb.connectors import get_connector
config = load_config('connectors.yaml')
conn = get_connector(config.connectors['redhat-kb'])
conn.authenticate()
print(conn.health())
"
```

Expected output:
```
{'ok': True, 'source': 'redhat-kb', 'total_issues': 48128, 'message': "Connected; ~48128 articles matching 'ceph'"}
```

### Notes
- **Offline tokens don't expire** — you only generate it once
- Token can be revoked at https://access.redhat.com/management/api
- Uses OAuth2: your offline token is exchanged for a short-lived bearer token automatically
- Requires an active Red Hat Customer Portal subscription
- Searches the Hydra KCS API for Ceph-related knowledge articles

---

## Enable Connectors

After setting up credentials, the active connectors are already enabled in `connectors.yaml`:

```yaml
connectors:
  ibm-jira:
    enabled: true      # Set to true after JIRA creds are ready

  redhat-kb:
    enabled: true      # Set to true after RH token is ready
```

## Run the Indexer

```bash
# Load credentials
source .env && export JIRA_USERNAME JIRA_API_TOKEN RH_OFFLINE_TOKEN

# Index all enabled connectors
python3 index_issues.py --config connectors.yaml --since 2024-01-01 --verbose

# Or index a single source
python3 index_issues.py --connector ibm-jira --verbose
python3 index_issues.py --connector redhat-kb --verbose
```

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `AuthError: Environment variable JIRA_API_TOKEN is not set or empty` | Env var not exported | Run `source .env && export JIRA_API_TOKEN` |
| `ConnectorError: RHKB request failed: 401` | Invalid offline token | Regenerate at https://access.redhat.com/management/api |
| `ConnectorError: JIRA request failed: 401` | Wrong username or token | Verify email and regenerate token |

## Security Best Practices

- **Never commit `.env`** — it's gitignored by default
- **Never put credentials in `connectors.yaml`** — it only stores env var *names*, not values
- **Rotate tokens periodically** — especially if shared or leaked
- **Use minimal permissions** — read-only access is sufficient for indexing
- **Revoke unused tokens** — delete tokens you no longer need

---

## Future Sources

The following sources have connector implementations but are not yet enabled. You do not need credentials for these until they are activated.

### Ceph Tracker (Redmine)

Public upstream issue tracker at https://tracker.ceph.com. No authentication required. The `RedmineConnector` is implemented in `src/ceph_issue_kb/connectors/redmine.py`.

### Red Hat Bugzilla

Red Hat bug tracker at https://bugzilla.redhat.com. Requires an API key generated at **Preferences** > **API Keys**. The `BugzillaConnector` is implemented in `src/ceph_issue_kb/connectors/bugzilla.py`.

| Source | Auth Method | Env Variable |
|--------|------------|-------------|
| Ceph Tracker | None | — |
| Red Hat Bugzilla | API Key | `BUGZILLA_API_KEY` |
