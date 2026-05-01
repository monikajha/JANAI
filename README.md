# JAN-AI

Demo workflow console for JAN API with:

- Keycloak authentication and role-based policies
- Model filtering by role
- Chat proxy endpoint
- Usage dashboard backed by JAN database tables

## What Is In This Repo

- `scripts/jan-demo-ui/index.html`:
	UI for dashboard, chat workflow, usage, and policy manager.
- `scripts/jan-demo-ui/app.py`:
	Local HTTP server and backend adapter (`/models`, `/chat`, `/usage`, `/policies`).
- `scripts/jan-demo-ui/policies.json`:
	Role policy configuration used by backend.
- `scripts/jan-demo-ui/start.sh`:
	Start helper for local UI/backend server.

## Prerequisites

You need the following services running locally:

1. Keycloak at `http://localhost:8085`
2. JAN API at `http://localhost:8000`
3. Python 3 available as `python3`

See [Dependencies](#dependencies) below for setup instructions.

## Setup: Keycloak (Local Development)

Use Docker Compose to start Keycloak locally:

```bash
cd infra/keycloak
docker-compose up -d
```

This starts:
- **Keycloak** at `http://localhost:8085`
- **PostgreSQL** for Keycloak (internal, port 5433)

Admin console:
- URL: `http://localhost:8085`
- Username: `admin`
- Password: `admin`

You will need to configure the `jan` realm and `jan-client` OAuth client in Keycloak manually or import a realm export file.

To tear down:
```bash
cd infra/keycloak
docker-compose down
```

## Quick Start

From repo root:

```bash
chmod +x scripts/jan-demo-ui/start.sh
./scripts/jan-demo-ui/start.sh 9000
```

Open:

- `http://localhost:9000`

Default login fields in UI:

- Keycloak Base: `http://localhost:8085`
- Realm: `jan`
- Client ID: `jan-client`

## API Behavior Summary

`app.py` exposes local endpoints that wrap JAN:

- `GET /models`
	- Reads JWT roles
	- Filters models by `policies.json`
- `POST /chat`
	- Enforces model allowlist + daily limits
	- Forwards normalized payload to `Pin Keycloak, run the automated cleanup script:

```bash
chmod +x scripts/cleanup-keycloak-guests.sh
./scripts/cleanup-keycloak-guests.sh
```

This script safely removes all users matching `guest-...@temp.jan.ai` from the `jan` realm.

Override Keycloak connection if needed:
```bash
KC_BASE=http://localhost:8085 KC_ADMIN_USER=admin KC_ADMIN_PASS=admin \
  ./scripts/cleanup-keycloak-guests.sh
```

The cleanup script is idempotent (safe to run multiple times)
	- Restricted by default to demo users (see below)
- `GET /policies` and `PUT /policies/{role}`
	- Admin-only (requires `jan_admin`)

## Demo User Scope (Important)

Usage is intentionally limited to demo users by default.

Current default allowlist in `app.py`:

- `monika@allerin.com`
- `duanetharp@tablesteaks.com`
- `premium.demo@allerin.com`

Override with env var:

```bash
export DEMO_ACTIVE_USERS="monika@allerin.com,duanetharp@tablesteaks.com,premium.demo@allerin.com"
./scripts/jan-demo-ui/start.sh 9000
```

## Keycloak Hygiene

If synthetic guest users accumulate and you want a clean demo list, remove users matching:

- `guest-...@temp.jan.ai`

Note: Deleting users in Keycloak changes runtime state only; it is not stored in git unless you commit automation scripts that perform the cleanup.

## Common Troubleshooting

### Port 9000 already in use

```bash
PIDS=$(/usr/sbin/lsof -nP -iTCP:9000 -sTCP:LISTEN | /usr/bin/awk 'NR>1{print $2}')
if [ -n "$PIDS" ]; then kill -9 $PIDS; fi
./scripts/jan-demo-ui/start.sh 9000
```

### Verify backend script syntax

```bash
python3 -m py_compile scripts/jan-demo-ui/app.py
```

### Verify usage endpoint quickly

```bash
curl -s "http://localhost:9000/usage?user=&days=30"
```

## Collaboration / Git Best Practices

1. Keep UI and backend changes in separate commits when possible.
2. Never commit secrets, tokens, or passwords.
3. Runtime admin actions (e.g., deleting users in Keycloak UI) should be documented in PR notes, not treated as code changes.
4. Include validation notes in PR:
	 - login works
	 - model filtering works

## Dependencies

### External Services Required

1. **JAN API Server** (`http://localhost:8000`)
   - Repository: [OwnersTable/ot-platform-infra](https://github.com/OwnersTable/ot-platform-infra)
   - Setup: Follow the JAN API README in that repo
   - This demo proxies requests to JAN's `/v1/models` and `/v1/chat/completions` endpoints

2. **Keycloak** (`http://localhost:8085`)
   - Start locally using Docker Compose (see [Setup: Keycloak](#setup-keycloak-local-development) above)
   - Realm: `jan`
   - Client: `jan-client`
   - Database: PostgreSQL (managed by Docker Compose)

### JAN API Database Tables

This demo reads usage data from:
- `llm_api.token_usage_daily` — Daily token aggregates
- `llm_api.token_usage` — Individual request logs

These tables are managed by the JAN API backend.
	 - chat works
	 - usage counts are expected

## Suggested Commit Scope

For this project, a clean change set is:

- `scripts/jan-demo-ui/index.html`
- `scripts/jan-demo-ui/app.py`
- `scripts/jan-demo-ui/policies.json`
- `scripts/jan-demo-ui/start.sh`