# Credential Setup Guide

This guide walks you through generating and configuring credentials for each issue source. The Ceph Tracker is public and needs no credentials. The other three sources require authentication.

## Overview

| Source | Auth Method | Credentials Needed | Env Variables |
|--------|------------|-------------------|---------------|
| Ceph Tracker | None | — | — |
| IBM Ceph JIRA | API Token | Email + token | `JIRA_USERNAME`, `JIRA_API_TOKEN` |
| Red Hat Bugzilla | API Key | API key | `BUGZILLA_API_KEY` |
| Red Hat KB | Session Cookie | SSO cookie | `RH_SSO_COOKIE` |

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

## Red Hat Bugzilla

### Generate API Key

1. Log in to [Red Hat Bugzilla](https://bugzilla.redhat.com)
2. Go to **Preferences** → **API Keys**: https://bugzilla.redhat.com/userprefs.cgi?tab=apikey
3. Under "New API key", enter a description: `ceph-issue-kb`
4. Click **Submit Changes**
5. Copy the generated API key

### Add to `.env`

```bash
BUGZILLA_API_KEY=aBcDeFgHiJkLmNoPqRsTuVwXyZ123456
```

### Verify

```bash
source .env && export BUGZILLA_API_KEY
python3 -c "
from ceph_issue_kb.config import load_config
from ceph_issue_kb.connectors import get_connector
config = load_config('connectors.yaml')
conn = get_connector(config.connectors['redhat-bugzilla'])
conn.authenticate()
print(conn.health())
"
```

### Notes
- API keys do not expire unless revoked
- Sent as `X-BUGZILLA-API-KEY` header
- Requires a Red Hat account with Bugzilla access
- Scoped to "Red Hat Ceph Storage" product (configured in `connectors.yaml`)

---

## Red Hat Knowledge Base

### Get Session Cookie

The Red Hat KB uses cookie-based authentication via Red Hat SSO. This is the most fragile method — cookies expire and must be refreshed periodically.

1. Log in to [Red Hat Customer Portal](https://access.redhat.com) in your browser
2. Open browser DevTools:
   - **Chrome/Edge**: `F12` → **Application** tab → **Cookies** → `https://access.redhat.com`
   - **Firefox**: `F12` → **Storage** tab → **Cookies** → `https://access.redhat.com`
   - **Safari**: **Develop** → **Show Web Inspector** → **Storage** → **Cookies**
3. Find the cookie named `rh_jwt` (a long JWT token starting with `eyJ...`)
4. Copy its **Value**

### Add to `.env`

```bash
RH_SSO_COOKIE=eyJhbGciOiJSUzI1NiIsInR5cCI...your_cookie_here
```

### Verify

```bash
source .env && export RH_SSO_COOKIE
python3 -c "
from ceph_issue_kb.config import load_config
from ceph_issue_kb.connectors import get_connector
config = load_config('connectors.yaml')
conn = get_connector(config.connectors['redhat-kb'])
conn.authenticate()
print(conn.health())
"
```

### Notes
- **Cookies expire** — typically after a few hours or when your browser session ends
- You will need to repeat steps 1-4 when the cookie expires
- If you get authentication errors, refresh the cookie
- Requires an active Red Hat Customer Portal subscription

---

## Enable Connectors

After setting up credentials, enable the connectors in `connectors.yaml`:

```yaml
connectors:
  ceph-tracker:
    enabled: true      # Always on — no auth needed

  ibm-jira:
    enabled: true      # Set to true after JIRA creds are ready

  redhat-bugzilla:
    enabled: true      # Set to true after Bugzilla key is ready

  redhat-kb:
    enabled: true      # Set to true after RH cookie is ready
```

## Run the Indexer

```bash
# Load credentials
source .env && export JIRA_USERNAME JIRA_API_TOKEN BUGZILLA_API_KEY RH_SSO_COOKIE

# Index all enabled connectors
python3 index_issues.py --config connectors.yaml --since 2024-01-01 --verbose

# Or index a single source
python3 index_issues.py --connector ceph-tracker --verbose
python3 index_issues.py --connector ibm-jira --verbose
python3 index_issues.py --connector redhat-bugzilla --verbose
python3 index_issues.py --connector redhat-kb --verbose
```

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `AuthError: Environment variable JIRA_API_TOKEN is not set or empty` | Env var not exported | Run `source .env && export JIRA_API_TOKEN` |
| `ConnectorError: Bugzilla authentication failed` | Invalid or expired API key | Regenerate at Bugzilla preferences |
| `ConnectorError: RHKB request failed: 401` | Expired SSO cookie | Re-extract `rh_sso` cookie from browser |
| `ConnectorError: Redmine request failed: 403` | Rate limited | Wait and retry, or reduce `rate_limit` in config |
| `ConnectorError: JIRA request failed: 401` | Wrong username or token | Verify email and regenerate token |

## Security Best Practices

- **Never commit `.env`** — it's gitignored by default
- **Never put credentials in `connectors.yaml`** — it only stores env var *names*, not values
- **Rotate tokens periodically** — especially if shared or leaked
- **Use minimal permissions** — read-only access is sufficient for indexing
- **Revoke unused tokens** — delete tokens you no longer need
