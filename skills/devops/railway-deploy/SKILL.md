---
name: railway-deploy
description: Self-deploy to Railway after making code changes. Commits and pushes to GitHub, then triggers a Railway redeploy so changes take effect cleanly without hot-patching the running process.
version: 1.0.0
author: Hermes Agent
license: MIT
prerequisites:
  env_vars: [RAILWAY_TOKEN]
  commands: [git, curl]
metadata:
  hermes:
    tags: [Railway, Deploy, Self-Deploy, CI/CD, DevOps]
    related_skills: [github-auth, webhook-subscriptions]
---

# Railway Self-Deploy

Use this skill when you've made code changes to yourself (the Hermes agent codebase) and need to deploy them. This triggers a clean rebuild and restart on Railway — **never hot-patch files on the running instance**.

## Why Not Hot-Patch?

Python caches imported modules in memory. Editing files on disk in a running container does NOT affect the current process. Killing PID 1 causes an uncontrolled restart that may lose state. The correct approach is: commit → push → trigger a new Railway deployment.

## Prerequisites

1. **Git authentication** — must be able to push to the repo (see `github-auth` skill)
2. **Railway token** — set `RAILWAY_TOKEN` in your environment or `~/.hermes/.env`
3. **Railway project/service IDs** — needed for the API call

### Finding Your Railway IDs

If you have the Railway CLI installed and linked:
```bash
# Show linked project info
railway status
```

Otherwise, check the CLAUDE.md or config for project/service IDs.

## Deploy Procedure

### Step 1: Verify You Have Changes

```bash
cd /opt/hermes  # or wherever the repo lives
git status
git diff --stat
```

If there are no changes, stop — there's nothing to deploy.

### Step 2: Commit Changes

```bash
git add -A
git commit -m "description of what changed and why"
```

Write a clear commit message. Future you (and your operator) will read these.

### Step 3: Push to GitHub

```bash
git push origin main
```

If the push fails due to auth, see the `github-auth` skill.
If the push fails due to diverged history, **do not force push**. Fetch and rebase first:

```bash
git fetch origin main
git rebase origin/main
git push origin main
```

### Step 4: Trigger Railway Redeploy

**Option A: Railway CLI (if installed)**

```bash
railway redeploy --service hermes-agent --yes
```

**Option B: Railway GraphQL API (always works)**

First, you need the latest deployment ID:

```bash
# Get the service's latest deployment ID
curl -s -X POST https://backboard.railway.app/graphql/v2 \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $RAILWAY_TOKEN" \
  -d '{
    "query": "query { deployments(first: 1, input: { serviceId: \"'$RAILWAY_SERVICE_ID'\" }) { edges { node { id status } } } }"
  }' | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['deployments']['edges'][0]['node']['id'])"
```

Then trigger the redeploy:

```bash
DEPLOYMENT_ID="<from above>"
curl -s -X POST https://backboard.railway.app/graphql/v2 \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $RAILWAY_TOKEN" \
  -d '{
    "query": "mutation { deploymentRedeploy(id: \"'$DEPLOYMENT_ID'\") { id status } }"
  }'
```

**Option C: One-liner redeploy via API**

```bash
# Combined: fetch latest deployment ID and redeploy in one script
python3 << 'PYEOF'
import json, os, urllib.request

TOKEN = os.environ.get("RAILWAY_TOKEN", "")
SERVICE_ID = os.environ.get("RAILWAY_SERVICE_ID", "")
API = "https://backboard.railway.app/graphql/v2"
HEADERS = {"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"}

# Get latest deployment
query = f'{{"query": "query {{ deployments(first: 1, input: {{ serviceId: \\"{SERVICE_ID}\\" }}) {{ edges {{ node {{ id status }} }} }} }}"}}'
req = urllib.request.Request(API, data=query.encode(), headers=HEADERS)
resp = json.loads(urllib.request.urlopen(req).read())
dep_id = resp["data"]["deployments"]["edges"][0]["node"]["id"]
print(f"Latest deployment: {dep_id}")

# Redeploy
mutation = f'{{"query": "mutation {{ deploymentRedeploy(id: \\"{dep_id}\\") {{ id status }} }}"}}'
req2 = urllib.request.Request(API, data=mutation.encode(), headers=HEADERS)
resp2 = json.loads(urllib.request.urlopen(req2).read())
new_dep = resp2["data"]["deploymentRedeploy"]
print(f"Redeploy triggered: {new_dep['id']} ({new_dep['status']})")
PYEOF
```

### Step 5: Confirm Deployment

After triggering the redeploy, tell the user:
- Changes have been committed and pushed to GitHub
- A new Railway deployment has been triggered
- The bot will restart within a few minutes with the updated code
- The current conversation will end when the container restarts

## Important Notes

- **Never edit files and expect the running process to pick them up.** Python caches modules.
- **Never `kill -15 1` or `kill -9 1`.** This causes an uncontrolled restart without a fresh build.
- **Always push to GitHub first.** If you only redeploy without pushing, the next build from source will lose your changes.
- **This will restart the gateway.** Active conversations will be interrupted. Session history is persisted to disk and will be available after restart.
- If the Railway CLI is available, prefer `railway redeploy` — it's simpler and handles the API details.
