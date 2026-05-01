# JAN-AI

Demo workflow console for JAN API with:

- Keycloak authentication and role-based policies
- Model filtering by role
- Chat proxy endpoint
- Usage dashboard backed by JAN database tables

## Repository Contents

- `scripts/jan-demo-ui/index.html`
  - Dashboard, workflow UI, chat view, usage view, policy manager UI.
- `scripts/jan-demo-ui/app.py`
  - Local server + adapter endpoints (`/models`, `/chat`, `/usage`, `/policies`).
- `scripts/jan-demo-ui/policies.json`
  - Role-based policy config.
- `scripts/jan-demo-ui/start.sh`
  - Starts demo UI/backend server.
- `infra/keycloak/docker-compose.yml`
  - Local Keycloak + PostgreSQL stack.
- `scripts/cleanup-keycloak-guests.sh`
  - Removes synthetic `guest-...@temp.jan.ai` users.
- `scripts/bootstrap-local.sh`
  - One-command bootstrap for local setup.

## Prerequisites

1. Docker + Docker Compose
2. Python 3 (`python3`)
3. JAN API running at `http://localhost:8000`

## Quick Start (One Command)

```bash
chmod +x scripts/bootstrap-local.sh scripts/jan-demo-ui/start.sh scripts/cleanup-keycloak-guests.sh
./scripts/bootstrap-local.sh
```

This will:

1. Start Keycloak stack from `infra/keycloak/docker-compose.yml`
2. Check JAN API readiness (best-effort)
3. Start demo UI/backend on port `9000`

Open:

- `http://localhost:9000`

## Manual Start (Step-by-Step)

### 1) Start Keycloak

```bash
cd infra/keycloak
docker-compose up -d
```

Keycloak admin console:

- URL: `http://localhost:8085`
- Username: `admin`
- Password: `admin`

### 2) Start Demo UI/Backend

```bash
cd ../..
chmod +x scripts/jan-demo-ui/start.sh
./scripts/jan-demo-ui/start.sh 9000
```

### 3) Login Defaults in UI

- Keycloak Base: `http://localhost:8085`
- Realm: `jan`
- Client ID: `jan-client`

## API Behavior Summary

`app.py` exposes local endpoints that wrap JAN:

- `GET /models`
  - Reads JWT roles
  - Filters models via `policies.json`
- `POST /chat`
  - Enforces model allowlist and daily limits
  - Forwards normalized payload to JAN `/v1/chat/completions`
- `GET /usage?user=&days=30`
  - Reads from JAN DB tables:
    - `llm_api.token_usage_daily`
    - `llm_api.token_usage`
  - Scoped by default to demo users
- `GET /policies`, `PUT /policies/{role}`
  - Admin-only (`jan_admin`)

## Demo User Scope

Default active usage scope in `app.py`:

- `monika@allerin.com`
- `duanetharp@tablesteaks.com`
- `premium.demo@allerin.com`

Override:

```bash
export DEMO_ACTIVE_USERS="monika@allerin.com,duanetharp@tablesteaks.com,premium.demo@allerin.com"
./scripts/jan-demo-ui/start.sh 9000
```

## Keycloak Hygiene

Remove synthetic guest users safely:

```bash
./scripts/cleanup-keycloak-guests.sh
```

Optional overrides:

```bash
KC_BASE=http://localhost:8085 KC_ADMIN_USER=admin KC_ADMIN_PASS=admin ./scripts/cleanup-keycloak-guests.sh
```

The cleanup script is idempotent (safe to run multiple times).

## Dependencies

### External Services

1. JAN API Server (`http://localhost:8000`)
   - Repository: `https://github.com/OwnersTable/ot-platform-infra`
   - This demo proxies to JAN endpoints such as `/v1/models` and `/v1/chat/completions`

2. Keycloak (`http://localhost:8085`)
   - Provided locally via Docker Compose in this repo

### JAN DB Tables Used

- `llm_api.token_usage_daily`
- `llm_api.token_usage`

## Troubleshooting

### Port 9000 already in use

```bash
PIDS=$(/usr/sbin/lsof -nP -iTCP:9000 -sTCP:LISTEN | /usr/bin/awk 'NR>1{print $2}')
if [ -n "$PIDS" ]; then kill -9 $PIDS; fi
./scripts/jan-demo-ui/start.sh 9000
```

### Validate backend syntax

```bash
python3 -m py_compile scripts/jan-demo-ui/app.py
```

### Quick usage API check

```bash
curl -s "http://localhost:9000/usage?user=&days=30"
```

## Collaboration Notes

1. Keep UI/backend/infra/docs changes in logical commits.
2. Never commit secrets or production credentials.
3. Runtime admin actions are environment state unless automated via scripts.
