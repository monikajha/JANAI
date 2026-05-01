#!/usr/bin/env python3
import base64
import json
import os
import re
import subprocess
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlparse
from urllib import error, request

DOCKER_BIN = "/usr/local/bin/docker"
COMPOSE_FILE = "/tmp/jan-server-local/docker-compose.yml"
DB_USER = "jan_user"
DB_NAME = "jan_llm_api"
JAN_BASE = "http://localhost:8000"
POLICIES_FILE = os.path.join(os.path.dirname(__file__), "policies.json")

SAFE_USER_RE = re.compile(r"^[a-zA-Z0-9@._\-]*$")

# By default, usage dashboard should focus on demo users only.
# Override with env var, e.g. DEMO_ACTIVE_USERS="a@x.com,b@y.com"
DEMO_ACTIVE_USERS = [
    u.strip()
    for u in os.environ.get(
        "DEMO_ACTIVE_USERS",
        "monika@allerin.com,duanetharp@tablesteaks.com,premium.demo@allerin.com",
    ).split(",")
    if u.strip()
]

DEFAULT_ROLE_POLICIES = {
    "jan_admin": {
        "name": "Admin",
        "default_model": "openai/gpt-4o",
        "allowed_models": ["*"],
        "max_requests_per_day": None,
        "max_tokens_per_day": None,
    },
    "jan_user_premium": {
        "name": "Premium",
        "default_model": "openai/gpt-4o",
        "allowed_models": [
            "openai/gpt-4o",
            "openai/gpt-4o-mini",
            "openai/gpt-4",
            "openai/gpt-3.5-turbo",
            "openai/gpt-5.4-mini",
        ],
        "max_requests_per_day": 2000,
        "max_tokens_per_day": 10_000_000,
    },
    "jan_user_standard": {
        "name": "Standard",
        "default_model": "openai/gpt-4o-mini",
        "allowed_models": [
            "openai/gpt-4o-mini",
            "openai/gpt-3.5-turbo",
            "openai/gpt-4",
        ],
        "max_requests_per_day": 500,
        "max_tokens_per_day": 1_000_000,
    },
    "default": {
        "name": "Fallback",
        "default_model": "openai/gpt-3.5-turbo",
        "allowed_models": ["openai/gpt-3.5-turbo"],
        "max_requests_per_day": 200,
        "max_tokens_per_day": 500_000,
    },
}

ROLE_POLICIES = {}


def run_psql(sql: str) -> str:
    cmd = [
        DOCKER_BIN,
        "compose",
        "-f",
        COMPOSE_FILE,
        "exec",
        "-T",
        "api-db",
        "psql",
        "-U",
        DB_USER,
        "-d",
        DB_NAME,
        "-At",
        "-F",
        "|",
        "-c",
        sql,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "psql execution failed")
    return result.stdout.strip()


def load_role_policies() -> dict:
    if not os.path.exists(POLICIES_FILE):
        return DEFAULT_ROLE_POLICIES
    with open(POLICIES_FILE, "r", encoding="utf-8") as f:
        loaded = json.load(f)

    if "default" not in loaded:
        raise ValueError("policies.json must include a 'default' policy")

    required_keys = {
        "name",
        "default_model",
        "allowed_models",
        "max_requests_per_day",
        "max_tokens_per_day",
    }
    for role, policy in loaded.items():
        missing = required_keys - set(policy.keys())
        if missing:
            raise ValueError(f"policy '{role}' missing keys: {sorted(missing)}")
        if not isinstance(policy["allowed_models"], list):
            raise ValueError(f"policy '{role}' field allowed_models must be a list")

    return loaded


def save_role_policies(policies: dict) -> None:
    required_keys = {
        "name",
        "default_model",
        "allowed_models",
        "max_requests_per_day",
        "max_tokens_per_day",
    }
    if "default" not in policies:
        raise ValueError("policies must include 'default'")

    for role, policy in policies.items():
        missing = required_keys - set(policy.keys())
        if missing:
            raise ValueError(f"policy '{role}' missing keys: {sorted(missing)}")
        if not isinstance(policy.get("allowed_models"), list):
            raise ValueError(f"policy '{role}' allowed_models must be a list")

    tmp_path = f"{POLICIES_FILE}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(policies, f, indent=2)
        f.write("\n")
    os.replace(tmp_path, POLICIES_FILE)


def require_admin_role(token: str) -> None:
    claims = decode_jwt_claims(token)
    roles = claims.get("realm_access", {}).get("roles", [])
    if "jan_admin" not in roles:
        raise PermissionError("jan_admin role required")


def decode_jwt_claims(token: str) -> dict:
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError("Invalid JWT format")
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    raw = base64.urlsafe_b64decode(payload + padding)
    return json.loads(raw.decode("utf-8"))


def resolve_policy(roles: list[str]) -> tuple[str, dict]:
    for role in ("jan_admin", "jan_user_premium", "jan_user_standard"):
        if role in roles:
            return role, ROLE_POLICIES[role]
    return "default", ROLE_POLICIES["default"]


def model_allowed(policy: dict, model_id: str) -> bool:
    allowed = policy["allowed_models"]
    if "*" in allowed:
        return True
    return model_id in allowed


def usage_today(username: str) -> dict:
    esc = username.replace("'", "''")
    sql = f"""
select
  coalesce(sum(d.request_count),0),
  coalesce(sum(d.total_tokens),0)
from llm_api.token_usage_daily d
left join llm_api.users u on cast(u.id as text) = d.user_id
where coalesce(u.username,'') = '{esc}'
  and d.usage_date = current_date;
"""
    out = run_psql(sql)
    if not out:
        return {"request_count": 0, "total_tokens": 0}
    parts = out.split("|")
    return {
        "request_count": int(parts[0] or 0),
        "total_tokens": int(parts[1] or 0),
    }


def normalize_chat_payload(payload: dict) -> dict:
    # Forward only a stable set of OpenAI-compatible keys to avoid client-specific extras
    # causing upstream 400 responses.
    allowed_keys = {
        "model",
        "messages",
        "stream",
        "temperature",
        "top_p",
        "max_tokens",
        "presence_penalty",
        "frequency_penalty",
        "stop",
        "n",
        "user",
        "response_format",
        "tools",
        "tool_choice",
        "max_completion_tokens",
        "prompt",
        "input",
    }
    cleaned = {k: v for k, v in payload.items() if k in allowed_keys and v is not None}

    # Normalize newer clients that send max_completion_tokens.
    if "max_completion_tokens" in cleaned and "max_tokens" not in cleaned:
        cleaned["max_tokens"] = cleaned["max_completion_tokens"]

    messages = cleaned.get("messages")
    if not isinstance(messages, list) or len(messages) == 0:
        # Fallback compatibility: clients may send prompt/input instead of messages.
        text = ""
        if isinstance(cleaned.get("prompt"), str):
            text = cleaned["prompt"]
        elif isinstance(cleaned.get("input"), str):
            text = cleaned["input"]
        elif isinstance(cleaned.get("input"), list):
            parts = []
            for item in cleaned["input"]:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    if isinstance(item.get("text"), str):
                        parts.append(item["text"])
                    elif isinstance(item.get("content"), str):
                        parts.append(item["content"])
            text = "\n".join([p for p in parts if p])
        if text:
            cleaned["messages"] = [{"role": "user", "content": text}]

    # Normalize rich content arrays into plain text for compatibility with older upstream models.
    norm_messages = []
    for m in cleaned.get("messages", []) if isinstance(cleaned.get("messages"), list) else []:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            texts = []
            for c in content:
                if isinstance(c, str):
                    texts.append(c)
                elif isinstance(c, dict) and isinstance(c.get("text"), str):
                    texts.append(c["text"])
            content = "\n".join([t for t in texts if t])
        if not isinstance(content, str):
            content = str(content)
        norm_messages.append({"role": role, "content": content})

    cleaned["messages"] = norm_messages

    if len(cleaned["messages"]) == 0:
        raise ValueError("messages must be a non-empty list")

    return cleaned


def jan_request(path: str, token: str, method: str = "GET", payload: Optional[dict] = None) -> tuple[int, dict]:
    body = None
    headers = {
        "Authorization": f"Bearer {token}",
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = request.Request(
        f"{JAN_BASE}{path}",
        data=body,
        method=method,
        headers=headers,
    )
    try:
        with request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8")
            return resp.status, json.loads(text) if text else {}
    except error.HTTPError as e:
        text = e.read().decode("utf-8")
        try:
            data = json.loads(text)
        except Exception:
            data = {"error": text or f"HTTP {e.code}"}
        return e.code, data


def usage_json(user_filter: str, days: int) -> dict:
    if not SAFE_USER_RE.match(user_filter):
        raise ValueError("Invalid user filter")

    where_summary = ""
    where_recent = ""
    summary_conditions = []
    recent_conditions = []

    if DEMO_ACTIVE_USERS:
        allowed = ",".join(["'" + u.replace("'", "''") + "'" for u in DEMO_ACTIVE_USERS])
        summary_conditions.append(f"coalesce(u.username,'') in ({allowed})")
        recent_conditions.append(f"coalesce(u.username,'') in ({allowed})")
    else:
        # Fallback: exclude synthetic guest users if no explicit allowlist is provided.
        summary_conditions.append("coalesce(u.username,'') not ilike 'guest-%@temp.jan.ai'")
        recent_conditions.append("coalesce(u.username,'') not ilike 'guest-%@temp.jan.ai'")

    summary_conditions.append(f"d.usage_date >= current_date - interval '{int(days)} days'")
    recent_conditions.append(f"t.created_at >= now() - interval '{int(days)} days'")

    if user_filter:
        esc = user_filter.replace("'", "''")
        summary_conditions.append(
            (
                "(coalesce(u.username,'') ilike '%{u}%' "
                "or coalesce(u.email,'') ilike '%{u}%' "
                "or d.user_id = '{u}')"
            ).format(u=esc)
        )
        recent_conditions.append(
            (
                "(coalesce(u.username,'') ilike '%{u}%' "
                "or coalesce(u.email,'') ilike '%{u}%' "
                "or t.user_id = '{u}')"
            ).format(u=esc)
        )

    where_summary = "where " + " and ".join(summary_conditions)
    where_recent = "where " + " and ".join(recent_conditions)

    summary_sql = f"""
select
  coalesce(u.username, d.user_id) as username,
  coalesce(u.email, '-') as email,
  sum(d.request_count) as requests,
  sum(d.total_prompt_tokens) as prompt_tokens,
  sum(d.total_completion_tokens) as completion_tokens,
  sum(d.total_tokens) as total_tokens,
  round(coalesce(sum(d.estimated_cost_usd),0)::numeric, 6) as cost_usd
from llm_api.token_usage_daily d
left join llm_api.users u on cast(u.id as text) = d.user_id
{where_summary}
group by coalesce(u.username, d.user_id), coalesce(u.email, '-')
order by total_tokens desc nulls last;
"""

    recent_sql = f"""
select
  to_char(t.created_at, 'YYYY-MM-DD HH24:MI:SS') as ts,
  coalesce(u.username, t.user_id) as username,
  t.model,
  t.provider,
  t.prompt_tokens,
  t.completion_tokens,
  t.total_tokens,
  round(coalesce(t.estimated_cost_usd,0)::numeric, 6) as cost_usd
from llm_api.token_usage t
left join llm_api.users u on cast(u.id as text) = t.user_id
{where_recent}
order by t.created_at desc
limit 25;
"""

    summary_rows = []
    out = run_psql(summary_sql)
    if out:
        for line in out.splitlines():
            parts = line.split("|")
            if len(parts) != 7:
                continue
            summary_rows.append(
                {
                    "username": parts[0],
                    "email": parts[1],
                    "requests": int(parts[2] or 0),
                    "prompt_tokens": int(parts[3] or 0),
                    "completion_tokens": int(parts[4] or 0),
                    "total_tokens": int(parts[5] or 0),
                    "cost_usd": parts[6],
                }
            )

    recent_rows = []
    out = run_psql(recent_sql)
    if out:
        for line in out.splitlines():
            parts = line.split("|")
            if len(parts) != 8:
                continue
            recent_rows.append(
                {
                    "ts": parts[0],
                    "username": parts[1],
                    "model": parts[2],
                    "provider": parts[3],
                    "prompt_tokens": int(parts[4] or 0),
                    "completion_tokens": int(parts[5] or 0),
                    "total_tokens": int(parts[6] or 0),
                    "cost_usd": parts[7],
                }
            )

    return {"summary": summary_rows, "recent": recent_rows}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.dirname(__file__), **kwargs)

    def _json(self, code: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bearer(self) -> str:
        auth = self.headers.get("Authorization", "")
        if not auth.lower().startswith("bearer "):
            raise ValueError("Missing Bearer token")
        return auth.split(" ", 1)[1].strip()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/usage":
            q = parse_qs(parsed.query)
            user = q.get("user", [""])[0].strip()
            days_raw = q.get("days", ["7"])[0].strip()
            try:
                days = int(days_raw)
                if days < 1 or days > 365:
                    raise ValueError("days out of range")
                data = usage_json(user, days)
                self._json(200, data)
            except Exception as e:
                self._json(400, {"error": str(e)})
            return

        if parsed.path == "/policies":
            try:
                token = self._bearer()
                require_admin_role(token)
                self._json(200, {"policies": ROLE_POLICIES})
            except PermissionError as e:
                self._json(403, {"error": str(e)})
            except Exception as e:
                self._json(400, {"error": str(e)})
            return

        if parsed.path in ("/models", "/v1/models"):
            try:
                token = self._bearer()
                claims = decode_jwt_claims(token)
                roles = claims.get("realm_access", {}).get("roles", [])
                username = claims.get("preferred_username", claims.get("sub", "unknown"))
                role, policy = resolve_policy(roles)

                status, data = jan_request("/v1/models", token)
                if status != 200:
                    self._json(status, data)
                    return

                filtered = [m for m in data.get("data", []) if model_allowed(policy, m.get("id", ""))]
                self._json(
                    200,
                    {
                        "object": "list",
                        "data": filtered,
                        "policy": {
                            "role": role,
                            "name": policy["name"],
                            "username": username,
                            "default_model": policy["default_model"],
                            "max_requests_per_day": policy["max_requests_per_day"],
                            "max_tokens_per_day": policy["max_tokens_per_day"],
                        },
                    },
                )
            except Exception as e:
                self._json(400, {"error": str(e)})
            return

        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path in ("/chat", "/chat/completions", "/v1/chat/completions"):
            try:
                token = self._bearer()
                claims = decode_jwt_claims(token)
                roles = claims.get("realm_access", {}).get("roles", [])
                username = claims.get("preferred_username", claims.get("sub", "unknown"))
                role, policy = resolve_policy(roles)

                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
                payload = json.loads(body)
                model = payload.get("model", "")

                if not model:
                    self._json(400, {"error": "model is required"})
                    return

                if not model_allowed(policy, model):
                    self._json(
                        403,
                        {
                            "error": "model_not_allowed",
                            "message": f"Model '{model}' is not allowed for role {role}.",
                        },
                    )
                    return

                today = usage_today(username)
                max_req = policy.get("max_requests_per_day")
                max_tok = policy.get("max_tokens_per_day")

                if max_req is not None and today["request_count"] >= int(max_req):
                    self._json(429, {"error": "quota_exceeded", "message": "Daily request quota exceeded"})
                    return

                if max_tok is not None and today["total_tokens"] >= int(max_tok):
                    self._json(429, {"error": "quota_exceeded", "message": "Daily token quota exceeded"})
                    return

                payload = normalize_chat_payload(payload)
                status, data = jan_request("/v1/chat/completions", token, method="POST", payload=payload)
                self._json(status, data)
            except Exception as e:
                self._json(400, {"error": str(e)})
            return

        self._json(404, {"error": "Not found"})
        return

    def do_PUT(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/policies/"):
            role = parsed.path.split("/", 2)[2].strip()
            try:
                token = self._bearer()
                require_admin_role(token)
                if role not in ROLE_POLICIES:
                    self._json(404, {"error": f"Unknown role: {role}"})
                    return

                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
                payload = json.loads(body)
                policy = payload.get("policy")
                if not isinstance(policy, dict):
                    self._json(400, {"error": "policy object is required"})
                    return

                updated = dict(ROLE_POLICIES)
                updated[role] = policy
                save_role_policies(updated)

                ROLE_POLICIES.clear()
                ROLE_POLICIES.update(load_role_policies())

                self._json(200, {"ok": True, "role": role, "policy": ROLE_POLICIES[role]})
            except PermissionError as e:
                self._json(403, {"error": str(e)})
            except Exception as e:
                self._json(400, {"error": str(e)})
            return

        self._json(404, {"error": "Not found"})
        return


if __name__ == "__main__":
    ROLE_POLICIES = load_role_policies()
    port = int(os.environ.get("PORT", "9000"))
    server = ThreadingHTTPServer(("", port), Handler)
    print(f"Serving Jan demo UI + usage API at http://localhost:{port}")
    server.serve_forever()
