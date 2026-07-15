# Dev/Manager Spaces + Model Promotion Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add JWT auth with dev/manager roles, an MLflow model-version comparison + promotion-request workflow (manager approval flips `@production`/`@staging` aliases), and hot-reloaded per-dataset model serving, deployed to GKE via the existing GitOps loop.

**Architecture:** FastAPI backend gains an auth layer (seeded users in Postgres, PyJWT bearer tokens, `require_user`/`require_manager` dependencies), a `models` router backed by a thin MLflow registry client and a Postgres `promotion_requests` store, and a per-dataset in-RAM model cache keyed on `models:/{dataset}-forecaster@production` with load-then-swap. The React frontend gains a real login page, an auth context, a Models page (versions + compare + request promote), and a manager-only Approvals page.

**Tech Stack:** FastAPI, psycopg3, MLflow 3.9 (registered-model aliases), PyJWT, bcrypt, React 18 + TypeScript + Tailwind (zinc/emerald design system), pytest + TestClient.

**Spec:** `docs/superpowers/specs/2026-07-15-dev-manager-spaces-design.md`

## Global Constraints

- MLflow is pinned `mlflow==3.9.0`; use **aliases** (`set_registered_model_alias`), never `transition_model_version_stage`.
- Roles are exactly `'dev'` and `'manager'`. JWT: HS256, TTL 12h, claims `sub` + `role`, secret from env `JWT_SECRET`.
- `/health`, `/version`, `/metrics`, and `POST /api/v1/auth/login` stay **unauthenticated** (k8s probes + Prometheus scrape + login).
- No passwords or secrets in the repo. Seed passwords come from env `SEED_DEV_PASSWORD` / `SEED_MANAGER_PASSWORD` at seed time.
- Frontend proxy quirk: the web proxy strips the first `/api`, so FastAPI paths with internal prefix `/api/v1/...` are called from the browser as `/api/api/v1/...` (existing `DATASET_API` pattern in `frontend/src/lib/api.ts`).
- Existing deployed behavior must not break: `/predict/csv` without a `dataset` field defaults to `hbaac_sku`; datasets without an `@production` alias fall back to `MLFLOW_MODEL_URI` env then pickle.
- Follow existing code style: dataclass settings in `config.py`, `app.state.*` singletons wired in `_lifespan`, psycopg with `dict_row`, tests via `TestClient(app)` with `app.state` mocks.
- Commit after every task with conventional-commit messages ending in the Claude co-author trailer.

---

### Task 1: Dependencies + DB schema migration

**Files:**
- Modify: `pyproject.toml` (dependencies list, around line 7-25)
- Create: `scripts/sprint_09_auth_promotion_schema.sql`

**Interfaces:**
- Produces: tables `mlops.users` and `mlops.promotion_requests` (columns below — Tasks 3 and 6 depend on the exact names), plus `pyjwt` and `bcrypt` importable.

- [ ] **Step 1: Add dependencies**

In `pyproject.toml`, inside the `dependencies = [` list, add two lines (keep alphabetical-ish placement near fastapi/mlflow):

```toml
    "bcrypt>=4.1",
    "pyjwt>=2.8",
```

- [ ] **Step 2: Write the migration SQL**

Create `scripts/sprint_09_auth_promotion_schema.sql`:

```sql
-- Sprint 09: auth users + model promotion approval workflow.

CREATE SCHEMA IF NOT EXISTS mlops;

CREATE TABLE IF NOT EXISTS mlops.users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('dev', 'manager')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mlops.promotion_requests (
    id BIGSERIAL PRIMARY KEY,
    dataset TEXT NOT NULL,
    model_name TEXT NOT NULL,
    candidate_version TEXT NOT NULL,
    current_prod_version TEXT,
    metrics_snapshot JSONB NOT NULL,
    requested_by TEXT NOT NULL,
    request_note TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    reviewed_by TEXT,
    review_comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_promotion_requests_status
    ON mlops.promotion_requests(status);
CREATE INDEX IF NOT EXISTS idx_promotion_requests_dataset
    ON mlops.promotion_requests(dataset, status);
```

- [ ] **Step 3: Install and sanity-check imports**

Run: `pip install -e . 2>&1 | tail -2 && python -c "import jwt, bcrypt; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml scripts/sprint_09_auth_promotion_schema.sql
git commit -m "feat(auth): add pyjwt/bcrypt deps + mlops schema migration"
```

---

### Task 2: Auth core (`api/app/auth.py`) — TDD

**Files:**
- Create: `api/app/auth.py`
- Modify: `api/app/config.py` (add `jwt_secret`)
- Test: `tests/test_auth_core.py`

**Interfaces:**
- Produces: `AuthUser(username: str, role: str)` frozen dataclass; `hash_password(str) -> str`; `verify_password(str, str) -> bool`; `create_token(user: AuthUser, secret: str, now: float | None = None) -> str`; `decode_token(token: str, secret: str) -> AuthUser` raising `AuthError`; constant `TOKEN_TTL_SECONDS = 43200`. `Settings.jwt_secret` (env `JWT_SECRET`, default `"dev-insecure-secret"`).

- [ ] **Step 1: Write failing tests**

Create `tests/test_auth_core.py`:

```python
"""Tests for JWT + password primitives (Sprint 09 auth)."""
from __future__ import annotations

import pytest

from api.app.auth import (
    AuthError,
    AuthUser,
    TOKEN_TTL_SECONDS,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)

SECRET = "test-secret"


def test_password_hash_roundtrip() -> None:
    digest = hash_password("s3cret")
    assert digest != "s3cret"
    assert verify_password("s3cret", digest)
    assert not verify_password("wrong", digest)


def test_verify_password_bad_hash_returns_false() -> None:
    assert not verify_password("x", "not-a-bcrypt-hash")


def test_token_roundtrip() -> None:
    user = AuthUser(username="dev1", role="dev")
    token = create_token(user, SECRET)
    assert decode_token(token, SECRET) == user


def test_expired_token_rejected() -> None:
    user = AuthUser(username="dev1", role="dev")
    token = create_token(user, SECRET, now=1_000_000.0)  # far past
    with pytest.raises(AuthError):
        decode_token(token, SECRET)


def test_wrong_secret_rejected() -> None:
    token = create_token(AuthUser(username="m1", role="manager"), SECRET)
    with pytest.raises(AuthError):
        decode_token(token, "other-secret")


def test_bad_role_claim_rejected() -> None:
    import jwt as pyjwt

    token = pyjwt.encode(
        {"sub": "x", "role": "root", "exp": 9_999_999_999}, SECRET, algorithm="HS256"
    )
    with pytest.raises(AuthError):
        decode_token(token, SECRET)


def test_ttl_is_12_hours() -> None:
    assert TOKEN_TTL_SECONDS == 12 * 3600
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_auth_core.py -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'api.app.auth'`

- [ ] **Step 3: Implement `api/app/auth.py`**

```python
"""JWT + password primitives for the dev/manager auth layer (Sprint 09)."""
from __future__ import annotations

import time
from dataclasses import dataclass

import bcrypt
import jwt

ALGORITHM = "HS256"
TOKEN_TTL_SECONDS = 12 * 3600
VALID_ROLES = {"dev", "manager"}


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class AuthUser:
    username: str
    role: str  # 'dev' | 'manager'


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def create_token(user: AuthUser, secret: str, now: float | None = None) -> str:
    issued = int(now if now is not None else time.time())
    payload = {
        "sub": user.username,
        "role": user.role,
        "iat": issued,
        "exp": issued + TOKEN_TTL_SECONDS,
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_token(token: str, secret: str) -> AuthUser:
    try:
        payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc)) from exc
    username = payload.get("sub")
    role = payload.get("role")
    if not username or role not in VALID_ROLES:
        raise AuthError("invalid token claims")
    return AuthUser(username=username, role=role)
```

- [ ] **Step 4: Add `jwt_secret` to Settings**

In `api/app/config.py` add to the `Settings` dataclass (after `redis_url`):

```python
    jwt_secret: str = "dev-insecure-secret"
```

and in `get_settings()` (after the `redis_url=` line):

```python
        jwt_secret=os.getenv("JWT_SECRET", defaults.jwt_secret),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_auth_core.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add api/app/auth.py api/app/config.py tests/test_auth_core.py
git commit -m "feat(auth): JWT + bcrypt primitives with jwt_secret setting"
```

---

### Task 3: User store, auth router, role dependencies — TDD

**Files:**
- Create: `api/app/infra/user_store.py`
- Create: `api/app/routers/auth.py`
- Create: `scripts/seed_users.py`
- Modify: `api/app/deps.py` (add `require_user`, `require_manager`, `get_user_store`)
- Modify: `api/app/schemas.py` (append auth schemas)
- Modify: `api/app/main.py` (wire `app.state.user_store`, include auth router)
- Test: `tests/test_auth_api.py`

**Interfaces:**
- Consumes: `AuthUser`, `create_token`, `decode_token`, `verify_password`, `hash_password` from Task 2.
- Produces: `UserStore(database_url).get_user(username) -> dict | None` (keys `username`, `password_hash`, `role`); FastAPI deps `require_user(...) -> AuthUser` and `require_manager(...) -> AuthUser` (Task 4/6 gate routes with these; tests override them by function identity); routes `POST /api/v1/auth/login` and `GET /api/v1/auth/me`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_auth_api.py`:

```python
"""Tests for /api/v1/auth login + me and role dependencies (Sprint 09)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from api.app.auth import hash_password
from api.app.main import app


@pytest.fixture
def client() -> TestClient:
    store = MagicMock()
    store.get_user.return_value = {
        "username": "dev1",
        "password_hash": hash_password("pw123"),
        "role": "dev",
    }
    app.state.user_store = store
    return TestClient(app)


def test_login_success_returns_token_and_role(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/auth/login", json={"username": "dev1", "password": "pw123"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["role"] == "dev"
    assert body["username"] == "dev1"
    assert body["access_token"]


def test_login_wrong_password_401(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/auth/login", json={"username": "dev1", "password": "nope"}
    )
    assert resp.status_code == 401


def test_login_unknown_user_401(client: TestClient) -> None:
    app.state.user_store.get_user.return_value = None
    resp = client.post(
        "/api/v1/auth/login", json={"username": "ghost", "password": "x"}
    )
    assert resp.status_code == 401


def test_me_roundtrip(client: TestClient) -> None:
    token = client.post(
        "/api/v1/auth/login", json={"username": "dev1", "password": "pw123"}
    ).json()["access_token"]
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"username": "dev1", "role": "dev"}


def test_me_without_token_401(client: TestClient) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_with_garbage_token_401(client: TestClient) -> None:
    resp = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer junk"})
    assert resp.status_code == 401
```

Note: these tests must run with the auth override from `tests/conftest.py` (Task 4) **disabled** for `/me` 401 cases — until Task 4 exists there is no override, so they pass as-is. After Task 4 adds the autouse override, `/me` resolves through the real `require_user` only when the override is popped; Task 4 Step 3 shows the pattern. To keep Task 3 self-contained, `test_me_without_token_401` and `test_me_with_garbage_token_401` must pop the override defensively:

```python
from api.app.deps import require_user

def test_me_without_token_401(client: TestClient) -> None:
    app.dependency_overrides.pop(require_user, None)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_with_garbage_token_401(client: TestClient) -> None:
    app.dependency_overrides.pop(require_user, None)
    resp = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer junk"})
    assert resp.status_code == 401
```

(Same defensive pop in `test_me_roundtrip` so the real decode path is exercised.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_auth_api.py -v`
Expected: FAIL (404 on `/api/v1/auth/login` — router not mounted yet)

- [ ] **Step 3: Implement `api/app/infra/user_store.py`**

```python
"""Postgres-backed user lookup for auth (Sprint 09)."""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row


class UserStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def get_user(self, username: str) -> dict[str, Any] | None:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT username, password_hash, role FROM mlops.users"
                    " WHERE username = %s",
                    (username,),
                )
                return cursor.fetchone()
```

- [ ] **Step 4: Add dependencies to `api/app/deps.py`**

Append (and extend the imports at the top of the file):

```python
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.app.auth import AuthError, AuthUser, decode_token
from api.app.config import get_settings
from api.app.infra.user_store import UserStore

_bearer = HTTPBearer(auto_error=False)


def get_user_store(request: Request) -> UserStore:
    return request.app.state.user_store


def require_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthUser:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        return decode_token(credentials.credentials, get_settings().jwt_secret)
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {exc}") from exc


def require_manager(user: AuthUser = Depends(require_user)) -> AuthUser:
    if user.role != "manager":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Manager role required")
    return user
```

- [ ] **Step 5: Append auth schemas to `api/app/schemas.py`**

```python
class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str


class MeResponse(BaseModel):
    username: str
    role: str
```

- [ ] **Step 6: Implement `api/app/routers/auth.py`**

```python
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from api.app.auth import AuthUser, create_token, verify_password
from api.app.config import get_settings
from api.app.deps import get_user_store, require_user
from api.app.infra.user_store import UserStore
from api.app.schemas import LoginRequest, LoginResponse, MeResponse

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
_logger = logging.getLogger(__name__)


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, store: UserStore = Depends(get_user_store)) -> LoginResponse:
    record = store.get_user(body.username)
    if record is None or not verify_password(body.password, record["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    user = AuthUser(username=record["username"], role=record["role"])
    token = create_token(user, get_settings().jwt_secret)
    return LoginResponse(access_token=token, username=user.username, role=user.role)


@router.get("/me", response_model=MeResponse)
def me(user: AuthUser = Depends(require_user)) -> MeResponse:
    return MeResponse(username=user.username, role=user.role)
```

- [ ] **Step 7: Wire into `api/app/main.py`**

In `_lifespan`, after `app.state.repository = ...` add:

```python
    app.state.user_store = UserStore(settings.database_url)
```

Add imports `from api.app.infra.user_store import UserStore` and `from api.app.routers import auth as auth_router_module`, and after the existing `app.include_router(...)` lines:

```python
app.include_router(auth_router_module.router)
```

- [ ] **Step 8: Write `scripts/seed_users.py`**

```python
"""Seed dev/manager users (idempotent upsert).

Env: DATABASE_URL, SEED_DEV_PASSWORD, SEED_MANAGER_PASSWORD.
Run inside the forecast-api pod or any env with DB access:
    python scripts/seed_users.py
"""
from __future__ import annotations

import os
import sys

import psycopg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from api.app.auth import hash_password  # noqa: E402

SEEDS = [
    ("dev1", "SEED_DEV_PASSWORD", "dev"),
    ("manager1", "SEED_MANAGER_PASSWORD", "manager"),
]


def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            for username, env_name, role in SEEDS:
                password = os.environ.get(env_name)
                if not password:
                    print(f"skip {username}: {env_name} not set")
                    continue
                cursor.execute(
                    """
                    INSERT INTO mlops.users (username, password_hash, role)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (username)
                    DO UPDATE SET password_hash = EXCLUDED.password_hash,
                                  role = EXCLUDED.role
                    """,
                    (username, hash_password(password), role),
                )
                print(f"seeded {username} ({role})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 9: Run tests**

Run: `pytest tests/test_auth_api.py tests/test_auth_core.py -v`
Expected: all pass

- [ ] **Step 10: Commit**

```bash
git add api/app/infra/user_store.py api/app/routers/auth.py api/app/deps.py \
  api/app/schemas.py api/app/main.py scripts/seed_users.py tests/test_auth_api.py
git commit -m "feat(auth): login/me endpoints, user store, role dependencies, user seeding"
```

---

### Task 4: Protect existing routes + tests conftest override

**Files:**
- Modify: `api/app/main.py` (router includes ~line 171-177; app-level forecast endpoints)
- Create: `tests/conftest.py`
- Test: `tests/test_auth_api.py` (extend)

**Interfaces:**
- Consumes: `require_user` from Task 3 (identity matters — `tests/conftest.py` overrides exactly `api.app.deps.require_user`).
- Produces: every business route 401s without a token; `tests/conftest.py` autouse fixture `_auth_override` that all existing test files rely on implicitly.

- [ ] **Step 1: Write failing test (extend `tests/test_auth_api.py`)**

```python
def test_business_routes_require_auth() -> None:
    from fastapi.testclient import TestClient as TC

    app.dependency_overrides.pop(require_user, None)
    bare = TC(app)
    assert bare.get("/forecast-runs/latest").status_code == 401
    assert bare.get("/api/v1/datasets").status_code == 401
    assert bare.post("/retrain/trigger", json={"reason": "x"}).status_code == 401
    # probes stay open (200 or 503 depending on DB, never 401)
    assert bare.get("/version").status_code == 200
    assert bare.get("/metrics").status_code == 200
```

(`from api.app.deps import require_user` is already imported per Task 3.)

Run: `pytest tests/test_auth_api.py::test_business_routes_require_auth -v`
Expected: FAIL (currently 200/404, not 401)

- [ ] **Step 2: Gate routers in `api/app/main.py`**

Add `from fastapi import Depends` to the existing fastapi import line and `from api.app.deps import require_user`. Replace the include block:

```python
_protected = [Depends(require_user)]

app.include_router(chat_router_module.router, dependencies=_protected)
app.include_router(datasets_router_module.router, dependencies=_protected)
app.include_router(predict_router_module.router, dependencies=_protected)
app.include_router(drift_router_module.router, dependencies=_protected)
app.include_router(retrain_router_module.router, dependencies=_protected)
app.include_router(ingest_router_module.router, dependencies=_protected)
app.include_router(auth_router_module.router)  # login open; /me self-protects
```

Add `dependencies=_protected` to each app-level business endpoint decorator — exactly these six: `@app.get("/forecast-runs/latest", ...)`, `@app.get("/model/current", ...)`, `@app.get("/forecast/top-skus", ...)`, `@app.get("/forecast/summary", ...)`, `@app.get("/forecast/{item_code}", ...)`, `@app.get("/monitoring/latest", ...)`. Example:

```python
@app.get("/forecast-runs/latest", response_model=ForecastRunResponse, dependencies=_protected)
```

Do NOT touch `/health`, `/version`, `/metrics`.

- [ ] **Step 3: Create `tests/conftest.py`**

```python
"""Shared fixtures: bypass auth for pre-Sprint-09 API tests."""
from __future__ import annotations

import pytest

from api.app.auth import AuthUser
from api.app.deps import require_user
from api.app.main import app


@pytest.fixture(autouse=True)
def _auth_override():
    app.dependency_overrides[require_user] = lambda: AuthUser(
        username="test-dev", role="dev"
    )
    yield
    app.dependency_overrides.pop(require_user, None)
```

- [ ] **Step 4: Run the full backend suite**

Run: `pytest tests/ -x -q --ignore=tests/load`
Expected: all pass (pre-existing failures unrelated to auth must be investigated, not skipped)

- [ ] **Step 5: Commit**

```bash
git add api/app/main.py tests/conftest.py tests/test_auth_api.py
git commit -m "feat(auth): require bearer token on all business routes"
```

---

### Task 5: MLflow registry client — TDD

**Files:**
- Create: `api/app/clients/mlflow_registry.py`
- Test: `tests/test_mlflow_registry.py`

**Interfaces:**
- Consumes: nothing new (MlflowClient underneath, injected for tests).
- Produces: `ModelRegistryClient(tracking_uri)` with:
  - `list_versions(model_name: str) -> list[dict]` — dicts `{version: str, run_id: str, created_at: int, aliases: list[str], metrics: dict[str, float]}` newest first
  - `get_alias_version(model_name: str, alias: str) -> str | None`
  - `compare(model_name: str, candidate_version: str) -> dict` — `{"candidate": <version dict>, "production": <version dict> | None}`; raises `ValueError` if candidate missing
  - `promote(model_name: str, candidate_version: str) -> str | None` — flips aliases, returns previous production version; **idempotent** (candidate already production → no-op, returns candidate)

  Metric keys exposed: `lightgbm_wape`, `lightgbm_mae`, `lightgbm_rmse`, `lightgbm_smape` (as logged by `train_and_log` in `src/hbacc_prj/training.py:270-278`).

- [ ] **Step 1: Write failing tests**

Create `tests/test_mlflow_registry.py`:

```python
"""Tests for the MLflow registry wrapper (Sprint 09)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from api.app.clients.mlflow_registry import ModelRegistryClient


def _mv(version: str, run_id: str, aliases: list[str]):
    return SimpleNamespace(
        version=version, run_id=run_id, creation_timestamp=int(version) * 1000,
        aliases=aliases,
    )


@pytest.fixture
def registry() -> ModelRegistryClient:
    reg = ModelRegistryClient("http://mlflow.test")
    client = MagicMock()
    client.search_model_versions.return_value = [
        _mv("1", "run-1", ["staging"]),
        _mv("2", "run-2", ["production"]),
    ]
    client.get_run.side_effect = lambda run_id: SimpleNamespace(
        data=SimpleNamespace(
            metrics={
                "lightgbm_wape": 0.5 if run_id == "run-1" else 0.6,
                "lightgbm_mae": 1.0,
                "lightgbm_rmse": 2.0,
                "lightgbm_smape": 0.3,
                "best_baseline_wape": 0.9,
            }
        )
    )
    reg._client = client  # inject mock
    return reg


def test_list_versions_newest_first_with_metrics(registry: ModelRegistryClient) -> None:
    versions = registry.list_versions("m")
    assert [v["version"] for v in versions] == ["2", "1"]
    assert versions[1]["aliases"] == ["staging"]
    assert versions[1]["metrics"]["lightgbm_wape"] == 0.5
    assert "best_baseline_wape" not in versions[1]["metrics"]


def test_get_alias_version(registry: ModelRegistryClient) -> None:
    assert registry.get_alias_version("m", "production") == "2"
    assert registry.get_alias_version("m", "champion") is None


def test_compare(registry: ModelRegistryClient) -> None:
    result = registry.compare("m", "1")
    assert result["candidate"]["version"] == "1"
    assert result["production"]["version"] == "2"


def test_compare_missing_candidate_raises(registry: ModelRegistryClient) -> None:
    with pytest.raises(ValueError):
        registry.compare("m", "99")


def test_promote_flips_aliases(registry: ModelRegistryClient) -> None:
    old = registry.promote("m", "1")
    assert old == "2"
    calls = registry._client.set_registered_model_alias.call_args_list
    assert calls[0].args == ("m", "production", "1")
    assert calls[1].args == ("m", "staging", "2")


def test_promote_idempotent_when_already_production(registry: ModelRegistryClient) -> None:
    assert registry.promote("m", "2") == "2"
    registry._client.set_registered_model_alias.assert_not_called()


def test_promote_first_time_no_old_production(registry: ModelRegistryClient) -> None:
    registry._client.search_model_versions.return_value = [_mv("1", "run-1", [])]
    assert registry.promote("m", "1") is None
    registry._client.set_registered_model_alias.assert_called_once_with(
        "m", "production", "1"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mlflow_registry.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `api/app/clients/mlflow_registry.py`**

```python
"""Thin wrapper over the MLflow model registry (aliases, MLflow 3.x).

Only the metric keys logged by train_and_log are exposed to the UI.
"""
from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)

METRIC_KEYS = ("lightgbm_wape", "lightgbm_mae", "lightgbm_rmse", "lightgbm_smape")


class ModelRegistryClient:
    def __init__(self, tracking_uri: str) -> None:
        self.tracking_uri = tracking_uri
        self._client: Any = None  # lazy; tests inject a mock

    def _mlflow(self) -> Any:
        if self._client is None:
            from mlflow.tracking import MlflowClient

            self._client = MlflowClient(tracking_uri=self.tracking_uri)
        return self._client

    def _run_metrics(self, run_id: str) -> dict[str, float]:
        try:
            metrics = self._mlflow().get_run(run_id).data.metrics
        except Exception:  # noqa: BLE001
            _logger.warning("Could not read run metrics for %s", run_id)
            return {}
        return {key: float(metrics[key]) for key in METRIC_KEYS if key in metrics}

    def list_versions(self, model_name: str) -> list[dict[str, Any]]:
        raw = self._mlflow().search_model_versions(f"name = '{model_name}'")
        versions = [
            {
                "version": str(mv.version),
                "run_id": mv.run_id,
                "created_at": int(mv.creation_timestamp),
                "aliases": list(mv.aliases or []),
                "metrics": self._run_metrics(mv.run_id) if mv.run_id else {},
            }
            for mv in raw
        ]
        versions.sort(key=lambda item: int(item["version"]), reverse=True)
        return versions

    def get_alias_version(self, model_name: str, alias: str) -> str | None:
        for version in self.list_versions(model_name):
            if alias in version["aliases"]:
                return version["version"]
        return None

    def compare(self, model_name: str, candidate_version: str) -> dict[str, Any]:
        versions = {v["version"]: v for v in self.list_versions(model_name)}
        candidate = versions.get(str(candidate_version))
        if candidate is None:
            raise ValueError(
                f"version {candidate_version} not found for model {model_name}"
            )
        production = next(
            (v for v in versions.values() if "production" in v["aliases"]), None
        )
        return {"candidate": candidate, "production": production}

    def promote(self, model_name: str, candidate_version: str) -> str | None:
        candidate_version = str(candidate_version)
        old = self.get_alias_version(model_name, "production")
        if old == candidate_version:
            return old
        client = self._mlflow()
        client.set_registered_model_alias(model_name, "production", candidate_version)
        if old is not None:
            client.set_registered_model_alias(model_name, "staging", old)
        return old
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mlflow_registry.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add api/app/clients/mlflow_registry.py tests/test_mlflow_registry.py
git commit -m "feat(models): MLflow registry client with alias promote/compare"
```

---

### Task 6: Promotion store + models router — TDD

**Files:**
- Create: `api/app/infra/promotion_store.py`
- Create: `api/app/routers/models.py`
- Modify: `api/app/schemas.py` (append promotion schemas)
- Modify: `api/app/deps.py` (add `get_promotion_store`, `get_model_registry`)
- Modify: `api/app/main.py` (wire state + router)
- Test: `tests/test_models_api.py`

**Interfaces:**
- Consumes: `ModelRegistryClient` (Task 5), `require_user`/`require_manager` (Task 3). Promotion always targets the **dataset model** `{dataset}-forecaster` (see `_model_names` in `api/app/routers/datasets.py:22-26`; the legacy `sku-demand-lightgbm` name is not promoted).
- Produces:
  - `PromotionStore(database_url)` with `create_request(...) -> dict`, `has_pending(dataset, candidate_version) -> bool`, `list_requests(status: str | None = None) -> list[dict]`, `get(request_id: int) -> dict | None`, `mark_reviewed(request_id, status, reviewed_by, comment) -> dict | None` (all dicts mirror the `mlops.promotion_requests` columns).
  - Routes under internal prefix `/api/v1`:
    - `GET /api/v1/models/{dataset}/versions`
    - `GET /api/v1/models/{dataset}/compare?candidate=N` (candidate optional — defaults to `@staging`)
    - `POST /api/v1/models/{dataset}/promotion-requests`
    - `GET /api/v1/promotion-requests?status=pending`
    - `POST /api/v1/promotion-requests/{id}/approve` / `.../reject` (manager only)
  - `app.state.model_cache.invalidate(dataset)` is called on approve — Task 7 provides it; until then `getattr(app.state, "model_cache", None)` guard.

- [ ] **Step 1: Write failing tests**

Create `tests/test_models_api.py`:

```python
"""Tests for model versions/compare + promotion request endpoints (Sprint 09)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from api.app.auth import AuthUser
from api.app.deps import require_user
from api.app.main import app

VERSIONS = [
    {"version": "2", "run_id": "r2", "created_at": 2000, "aliases": ["staging"],
     "metrics": {"lightgbm_wape": 0.4}},
    {"version": "1", "run_id": "r1", "created_at": 1000, "aliases": ["production"],
     "metrics": {"lightgbm_wape": 0.5}},
]


def _request_row(**overrides):
    row = {
        "id": 1, "dataset": "hbaac_sku", "model_name": "hbaac_sku-forecaster",
        "candidate_version": "2", "current_prod_version": "1",
        "metrics_snapshot": {"candidate": VERSIONS[0], "production": VERSIONS[1]},
        "requested_by": "test-dev", "request_note": None, "status": "pending",
        "reviewed_by": None, "review_comment": None,
        "created_at": "2026-07-15T00:00:00Z", "reviewed_at": None,
    }
    row.update(overrides)
    return row


@pytest.fixture
def registry() -> MagicMock:
    reg = MagicMock()
    reg.list_versions.return_value = VERSIONS
    reg.compare.return_value = {"candidate": VERSIONS[0], "production": VERSIONS[1]}
    reg.get_alias_version.side_effect = lambda name, alias: {
        "production": "1", "staging": "2"
    }.get(alias)
    reg.promote.return_value = "1"
    return reg


@pytest.fixture
def store() -> MagicMock:
    st = MagicMock()
    st.has_pending.return_value = False
    st.create_request.return_value = _request_row()
    st.get.return_value = _request_row()
    st.list_requests.return_value = [_request_row()]
    st.mark_reviewed.return_value = _request_row(
        status="approved", reviewed_by="m1"
    )
    return st


@pytest.fixture
def client(registry: MagicMock, store: MagicMock) -> TestClient:
    app.state.model_registry = registry
    app.state.promotion_store = store
    app.state.model_cache = MagicMock()
    return TestClient(app)


def _as_manager():
    app.dependency_overrides[require_user] = lambda: AuthUser(
        username="m1", role="manager"
    )


def test_list_versions(client: TestClient) -> None:
    resp = client.get("/api/v1/models/hbaac_sku/versions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["model_name"] == "hbaac_sku-forecaster"
    assert [v["version"] for v in body["versions"]] == ["2", "1"]


def test_compare_defaults_to_staging(client: TestClient, registry: MagicMock) -> None:
    resp = client.get("/api/v1/models/hbaac_sku/compare")
    assert resp.status_code == 200
    registry.compare.assert_called_with("hbaac_sku-forecaster", "2")


def test_compare_no_staging_404(client: TestClient, registry: MagicMock) -> None:
    registry.get_alias_version.side_effect = lambda name, alias: None
    assert client.get("/api/v1/models/hbaac_sku/compare").status_code == 404


def test_create_request(client: TestClient, store: MagicMock) -> None:
    resp = client.post(
        "/api/v1/models/hbaac_sku/promotion-requests",
        json={"candidate_version": "2", "note": "better wape"},
    )
    assert resp.status_code == 200, resp.text
    assert store.create_request.call_args.kwargs["requested_by"] == "test-dev"


def test_create_request_duplicate_409(client: TestClient, store: MagicMock) -> None:
    store.has_pending.return_value = True
    resp = client.post(
        "/api/v1/models/hbaac_sku/promotion-requests",
        json={"candidate_version": "2"},
    )
    assert resp.status_code == 409


def test_create_request_candidate_is_production_409(
    client: TestClient, registry: MagicMock
) -> None:
    resp = client.post(
        "/api/v1/models/hbaac_sku/promotion-requests",
        json={"candidate_version": "1"},
    )
    assert resp.status_code == 409


def test_approve_requires_manager(client: TestClient) -> None:
    resp = client.post("/api/v1/promotion-requests/1/approve", json={})
    assert resp.status_code == 403


def test_approve_flips_alias_and_invalidates_cache(
    client: TestClient, registry: MagicMock, store: MagicMock
) -> None:
    _as_manager()
    resp = client.post("/api/v1/promotion-requests/1/approve", json={})
    assert resp.status_code == 200, resp.text
    registry.promote.assert_called_once_with("hbaac_sku-forecaster", "2")
    app.state.model_cache.invalidate.assert_called_once_with("hbaac_sku")
    assert store.mark_reviewed.call_args.args[1] == "approved"


def test_approve_missing_candidate_409(
    client: TestClient, registry: MagicMock
) -> None:
    _as_manager()
    registry.list_versions.return_value = [VERSIONS[1]]  # only v1 exists
    assert client.post("/api/v1/promotion-requests/1/approve", json={}).status_code == 409


def test_approve_already_reviewed_409(client: TestClient, store: MagicMock) -> None:
    _as_manager()
    store.get.return_value = _request_row(status="approved")
    assert client.post("/api/v1/promotion-requests/1/approve", json={}).status_code == 409


def test_reject_records_comment(client: TestClient, store: MagicMock) -> None:
    _as_manager()
    store.mark_reviewed.return_value = _request_row(status="rejected")
    resp = client.post(
        "/api/v1/promotion-requests/1/reject", json={"comment": "not enough gain"}
    )
    assert resp.status_code == 200
    assert store.mark_reviewed.call_args.args[1] == "rejected"


def test_list_requests_filter(client: TestClient, store: MagicMock) -> None:
    resp = client.get("/api/v1/promotion-requests?status=pending")
    assert resp.status_code == 200
    store.list_requests.assert_called_with(status="pending")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_models_api.py -v`
Expected: FAIL (404s — router missing)

- [ ] **Step 3: Implement `api/app/infra/promotion_store.py`**

```python
"""Postgres store for model promotion requests (Sprint 09)."""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

_COLUMNS = (
    "id, dataset, model_name, candidate_version, current_prod_version,"
    " metrics_snapshot, requested_by, request_note, status, reviewed_by,"
    " review_comment, created_at, reviewed_at"
)


class PromotionStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def create_request(
        self,
        *,
        dataset: str,
        model_name: str,
        candidate_version: str,
        current_prod_version: str | None,
        metrics_snapshot: dict[str, Any],
        requested_by: str,
        request_note: str | None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO mlops.promotion_requests
                        (dataset, model_name, candidate_version, current_prod_version,
                         metrics_snapshot, requested_by, request_note)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING {_COLUMNS}
                    """,
                    (
                        dataset, model_name, candidate_version, current_prod_version,
                        Jsonb(metrics_snapshot), requested_by, request_note,
                    ),
                )
                return cursor.fetchone()  # type: ignore[return-value]

    def has_pending(self, dataset: str, candidate_version: str) -> bool:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM mlops.promotion_requests"
                    " WHERE dataset = %s AND candidate_version = %s"
                    " AND status = 'pending' LIMIT 1",
                    (dataset, candidate_version),
                )
                return cursor.fetchone() is not None

    def list_requests(self, status: str | None = None) -> list[dict[str, Any]]:
        query = f"SELECT {_COLUMNS} FROM mlops.promotion_requests"
        params: list[Any] = []
        if status:
            query += " WHERE status = %s"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT 200"
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, params)
                return list(cursor.fetchall())

    def get(self, request_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT {_COLUMNS} FROM mlops.promotion_requests WHERE id = %s",
                    (request_id,),
                )
                return cursor.fetchone()

    def mark_reviewed(
        self, request_id: int, status: str, reviewed_by: str, comment: str | None
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE mlops.promotion_requests
                    SET status = %s, reviewed_by = %s, review_comment = %s,
                        reviewed_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    RETURNING {_COLUMNS}
                    """,
                    (status, reviewed_by, comment, request_id),
                )
                return cursor.fetchone()
```

- [ ] **Step 4: Append promotion schemas to `api/app/schemas.py`**

```python
class ModelVersionItem(BaseModel):
    version: str
    run_id: str | None = None
    created_at: int
    aliases: list[str]
    metrics: dict[str, float]


class ModelVersionsResponse(BaseModel):
    dataset: str
    model_name: str
    versions: list[ModelVersionItem]


class ModelCompareResponse(BaseModel):
    dataset: str
    model_name: str
    candidate: ModelVersionItem
    production: ModelVersionItem | None = None


class PromotionRequestCreate(BaseModel):
    candidate_version: str
    note: str | None = None


class PromotionReviewBody(BaseModel):
    comment: str | None = None


class PromotionRequestItem(BaseModel):
    id: int
    dataset: str
    model_name: str
    candidate_version: str
    current_prod_version: str | None = None
    metrics_snapshot: dict
    requested_by: str
    request_note: str | None = None
    status: str
    reviewed_by: str | None = None
    review_comment: str | None = None
    created_at: datetime
    reviewed_at: datetime | None = None


class PromotionRequestListResponse(BaseModel):
    items: list[PromotionRequestItem]
```

(`datetime` is already imported in `schemas.py`; verify and add `from datetime import datetime` if not.)

- [ ] **Step 5: Add deps in `api/app/deps.py`**

```python
from api.app.clients.mlflow_registry import ModelRegistryClient
from api.app.infra.promotion_store import PromotionStore


def get_promotion_store(request: Request) -> PromotionStore:
    return request.app.state.promotion_store


def get_model_registry(request: Request) -> ModelRegistryClient:
    return request.app.state.model_registry
```

- [ ] **Step 6: Implement `api/app/routers/models.py`**

```python
"""Model registry views + promotion approval workflow (Sprint 09)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from api.app.auth import AuthUser
from api.app.clients.mlflow_registry import ModelRegistryClient
from api.app.deps import (
    get_model_registry,
    get_promotion_store,
    require_manager,
    require_user,
)
from api.app.infra.promotion_store import PromotionStore
from api.app.schemas import (
    ModelCompareResponse,
    ModelVersionsResponse,
    PromotionRequestCreate,
    PromotionRequestItem,
    PromotionRequestListResponse,
    PromotionReviewBody,
)

router = APIRouter(prefix="/api/v1", tags=["models"])
_logger = logging.getLogger(__name__)


def _dataset_model(dataset: str) -> str:
    return f"{dataset}-forecaster"


@router.get("/models/{dataset}/versions", response_model=ModelVersionsResponse)
def list_model_versions(
    dataset: str,
    registry: ModelRegistryClient = Depends(get_model_registry),
) -> ModelVersionsResponse:
    model_name = _dataset_model(dataset)
    try:
        versions = registry.list_versions(model_name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"MLflow error: {exc}") from exc
    return ModelVersionsResponse(dataset=dataset, model_name=model_name, versions=versions)


@router.get("/models/{dataset}/compare", response_model=ModelCompareResponse)
def compare_model_versions(
    dataset: str,
    candidate: str | None = Query(default=None),
    registry: ModelRegistryClient = Depends(get_model_registry),
) -> ModelCompareResponse:
    model_name = _dataset_model(dataset)
    try:
        target = candidate or registry.get_alias_version(model_name, "staging")
        if target is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "No candidate: no @staging version exists"
            )
        result = registry.compare(model_name, target)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"MLflow error: {exc}") from exc
    return ModelCompareResponse(
        dataset=dataset, model_name=model_name,
        candidate=result["candidate"], production=result["production"],
    )


@router.post("/models/{dataset}/promotion-requests", response_model=PromotionRequestItem)
def create_promotion_request(
    dataset: str,
    body: PromotionRequestCreate,
    user: AuthUser = Depends(require_user),
    registry: ModelRegistryClient = Depends(get_model_registry),
    store: PromotionStore = Depends(get_promotion_store),
) -> PromotionRequestItem:
    model_name = _dataset_model(dataset)
    try:
        prod_version = registry.get_alias_version(model_name, "production")
        snapshot = registry.compare(model_name, body.candidate_version)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"MLflow error: {exc}") from exc
    if prod_version == body.candidate_version:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Candidate is already the production version"
        )
    if store.has_pending(dataset, body.candidate_version):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A pending request for this version already exists",
        )
    row = store.create_request(
        dataset=dataset,
        model_name=model_name,
        candidate_version=body.candidate_version,
        current_prod_version=prod_version,
        metrics_snapshot=snapshot,
        requested_by=user.username,
        request_note=body.note,
    )
    return PromotionRequestItem(**row)


@router.get("/promotion-requests", response_model=PromotionRequestListResponse)
def list_promotion_requests(
    status_filter: str | None = Query(default=None, alias="status"),
    store: PromotionStore = Depends(get_promotion_store),
) -> PromotionRequestListResponse:
    rows = store.list_requests(status=status_filter)
    return PromotionRequestListResponse(items=[PromotionRequestItem(**r) for r in rows])


def _load_pending(store: PromotionStore, request_id: int) -> dict:
    row = store.get(request_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "request not found")
    if row["status"] != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"request already {row['status']}"
        )
    return row


@router.post("/promotion-requests/{request_id}/approve", response_model=PromotionRequestItem)
def approve_promotion_request(
    request_id: int,
    body: PromotionReviewBody,
    request: Request,
    user: AuthUser = Depends(require_manager),
    registry: ModelRegistryClient = Depends(get_model_registry),
    store: PromotionStore = Depends(get_promotion_store),
) -> PromotionRequestItem:
    row = _load_pending(store, request_id)
    try:
        existing = {v["version"] for v in registry.list_versions(row["model_name"])}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"MLflow error: {exc}") from exc
    if row["candidate_version"] not in existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "candidate version no longer exists in MLflow"
        )
    try:
        registry.promote(row["model_name"], row["candidate_version"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"MLflow alias update failed: {exc}"
        ) from exc
    cache = getattr(request.app.state, "model_cache", None)
    if cache is not None:
        cache.invalidate(row["dataset"])
    updated = store.mark_reviewed(request_id, "approved", user.username, body.comment)
    return PromotionRequestItem(**(updated or row))


@router.post("/promotion-requests/{request_id}/reject", response_model=PromotionRequestItem)
def reject_promotion_request(
    request_id: int,
    body: PromotionReviewBody,
    user: AuthUser = Depends(require_manager),
    store: PromotionStore = Depends(get_promotion_store),
) -> PromotionRequestItem:
    _load_pending(store, request_id)
    updated = store.mark_reviewed(request_id, "rejected", user.username, body.comment)
    return PromotionRequestItem(**updated)  # type: ignore[arg-type]
```

- [ ] **Step 7: Wire into `api/app/main.py`**

Imports: `from api.app.clients.mlflow_registry import ModelRegistryClient`, `from api.app.infra.promotion_store import PromotionStore`, `from api.app.routers import models as models_router_module`.

In `_lifespan` after `app.state.user_store = ...`:

```python
    app.state.promotion_store = PromotionStore(settings.database_url)
    app.state.model_registry = ModelRegistryClient(settings.mlflow_tracking_uri)
```

After the router includes:

```python
app.include_router(models_router_module.router, dependencies=_protected)
```

Note: `require_user` in `create_promotion_request` resolves via the router-level dependency too; both are fine (FastAPI dedupes by dependency).

- [ ] **Step 8: Run tests**

Run: `pytest tests/test_models_api.py tests/test_auth_api.py -v`
Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add api/app/infra/promotion_store.py api/app/routers/models.py \
  api/app/schemas.py api/app/deps.py api/app/main.py tests/test_models_api.py
git commit -m "feat(models): promotion request workflow with manager approval"
```

---

### Task 7: Per-dataset model cache with load-then-swap — TDD

**Files:**
- Create: `api/app/clients/model_cache.py`
- Modify: `api/app/routers/predict.py` (use cache + `dataset` form field)
- Modify: `api/app/main.py` (wire `app.state.model_cache`, drop `app.state.model = None`)
- Test: `tests/test_model_cache.py`, extend `tests/test_predict_csv.py`

**Interfaces:**
- Consumes: `load_model` fallback logic in `api/app/clients/mlflow_loader.py:21` (reused for the default dataset's env/pickle fallback).
- Produces: `ModelCache(tracking_uri, fallback_model_uri="", fallback_path=None, default_dataset="hbaac_sku")` with `get(dataset: str) -> Any | None` and `invalidate(dataset: str) -> None`. Task 6's approve endpoint calls `invalidate`; predict calls `get`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_model_cache.py`:

```python
"""Tests for the per-dataset model cache (Sprint 09)."""
from __future__ import annotations

from unittest.mock import MagicMock

from api.app.clients.model_cache import ModelCache


def _cache(loader: MagicMock) -> ModelCache:
    cache = ModelCache("http://mlflow.test")
    cache._load_alias_model = loader  # inject
    return cache


def test_get_loads_once_and_caches() -> None:
    loader = MagicMock(return_value="model-v1")
    cache = _cache(loader)
    assert cache.get("ds") == "model-v1"
    assert cache.get("ds") == "model-v1"
    loader.assert_called_once_with("ds")


def test_invalidate_triggers_reload() -> None:
    loader = MagicMock(side_effect=["model-v1", "model-v2"])
    cache = _cache(loader)
    assert cache.get("ds") == "model-v1"
    cache.invalidate("ds")
    assert cache.get("ds") == "model-v2"
    assert loader.call_count == 2


def test_load_then_swap_keeps_old_model_on_failure() -> None:
    loader = MagicMock(side_effect=["model-v1", RuntimeError("boom"), "model-v2"])
    cache = _cache(loader)
    assert cache.get("ds") == "model-v1"
    cache.invalidate("ds")
    assert cache.get("ds") == "model-v1"  # reload failed -> old model survives
    assert cache.get("ds") == "model-v2"  # retried next call
    assert loader.call_count == 3


def test_no_model_available_returns_none() -> None:
    loader = MagicMock(return_value=None)
    cache = _cache(loader)
    assert cache.get("ds") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_model_cache.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `api/app/clients/model_cache.py`**

```python
"""Per-dataset production-model cache with hot reload (Sprint 09).

get(dataset) loads models:/{dataset}-forecaster@production once and serves it
from RAM. invalidate(dataset) marks the entry stale; the next get() attempts a
reload and keeps the old model if the reload fails (load-then-swap).
The default dataset keeps the legacy MLFLOW_MODEL_URI / pickle fallback.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from api.app.clients.mlflow_loader import ModelLoadError, load_model

_logger = logging.getLogger(__name__)


@dataclass
class _Entry:
    model: Any
    stale: bool = False


class ModelCache:
    def __init__(
        self,
        tracking_uri: str,
        fallback_model_uri: str = "",
        fallback_path: str | None = None,
        default_dataset: str = "hbaac_sku",
    ) -> None:
        self.tracking_uri = tracking_uri
        self.fallback_model_uri = fallback_model_uri
        self.fallback_path = fallback_path
        self.default_dataset = default_dataset
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def _load_alias_model(self, dataset: str) -> Any | None:
        alias_uri = f"models:/{dataset}-forecaster@production"
        try:
            import mlflow  # type: ignore

            mlflow.set_tracking_uri(self.tracking_uri)
            _logger.info("Loading production model %s", alias_uri)
            return mlflow.pyfunc.load_model(alias_uri)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Alias load failed for %s (%s)", alias_uri, exc)
        if dataset == self.default_dataset and (
            self.fallback_model_uri or self.fallback_path
        ):
            try:
                return load_model(
                    self.tracking_uri, self.fallback_model_uri, self.fallback_path
                )
            except ModelLoadError:
                _logger.warning("Fallback load failed for %s", dataset)
        return None

    def get(self, dataset: str) -> Any | None:
        with self._lock:
            entry = self._entries.get(dataset)
            if entry is not None and not entry.stale:
                return entry.model
        try:
            model = self._load_alias_model(dataset)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Model reload raised for %s (%s)", dataset, exc)
            model = None
        with self._lock:
            entry = self._entries.get(dataset)
            if model is not None:
                self._entries[dataset] = _Entry(model=model, stale=False)
                return model
            if entry is not None:
                # load-then-swap: keep serving the old model, stay stale to retry
                return entry.model
            self._entries[dataset] = _Entry(model=None, stale=True)
            return None

    def invalidate(self, dataset: str) -> None:
        with self._lock:
            entry = self._entries.get(dataset)
            if entry is not None:
                entry.stale = True
```

Behavior note: an entry with `model=None, stale=True` retries the load on every `get` — intentional, so a dataset's first promotion activates without a restart.

- [ ] **Step 4: Run cache tests**

Run: `pytest tests/test_model_cache.py -v`
Expected: 4 passed

- [ ] **Step 5: Use the cache in predict**

In `api/app/routers/predict.py`:
- Add `Form` to the fastapi import.
- Change the endpoint signature:

```python
@router.post("/csv", response_model=PredictJobResponse)
async def predict_csv(
    request: Request,
    file: UploadFile = File(...),
    dataset: str = Form("hbaac_sku"),
    airflow: AirflowClient = Depends(get_airflow_client),
) -> PredictJobResponse:
```

- Pass it through: `items, chart_spec = _predict_inline(df, request, dataset)`.
- Change `_predict_inline`:

```python
def _predict_inline(
    df: pd.DataFrame, request: Request, dataset: str
) -> tuple[list[PredictPoint], dict[str, Any]]:
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "ItemCode"])

    cache = getattr(request.app.state, "model_cache", None)
    model = cache.get(dataset) if cache is not None else None
    if model is not None and hasattr(model, "predict"):
        ...
```

(the rest of `_predict_inline` is unchanged).

- [ ] **Step 6: Wire cache in `api/app/main.py`**

Import `from api.app.clients.model_cache import ModelCache`. In `_lifespan`, replace `app.state.model = None` with:

```python
    app.state.model_cache = ModelCache(
        tracking_uri=settings.mlflow_tracking_uri,
        fallback_model_uri=settings.mlflow_model_uri,
        fallback_path=settings.production_submission_path or None,
    )
```

- [ ] **Step 7: Extend `tests/test_predict_csv.py`**

In the `client` fixture, add a mock cache that returns no model (baseline path):

```python
    from unittest.mock import MagicMock

    cache = MagicMock()
    cache.get.return_value = None
    app.state.model_cache = cache
```

Add a new test:

```python
def test_predict_csv_passes_dataset_to_cache(client: TestClient) -> None:
    files = {"file": ("sample.csv", io.BytesIO(_csv_bytes(5)), "text/csv")}
    resp = client.post("/predict/csv", files=files, data={"dataset": "sample_shop"})
    assert resp.status_code == 200, resp.text
    app.state.model_cache.get.assert_called_with("sample_shop")
```

- [ ] **Step 8: Run suite**

Run: `pytest tests/test_model_cache.py tests/test_predict_csv.py -v`
Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add api/app/clients/model_cache.py api/app/routers/predict.py \
  api/app/main.py tests/test_model_cache.py tests/test_predict_csv.py
git commit -m "feat(serving): per-dataset @production model cache with hot reload"
```

---

### Task 8: Training uses aliases; alias bootstrap script

**Files:**
- Modify: `src/hbacc_prj/training.py:305-324` (registration block)
- Create: `scripts/bootstrap_model_aliases.py`
- Test: existing `tests/test_sprint_04_training.py` must stay green

**Interfaces:**
- Consumes: `MlflowClient.set_registered_model_alias` (MLflow 3.x).
- Produces: after a passing train run, the new version of each registered model carries alias `staging` (no stage transitions anywhere).

- [ ] **Step 1: Check existing test expectations**

Run: `grep -n "transition_model_version_stage\|Staging\|staging" tests/test_sprint_04_training.py src/hbacc_prj/training.py`
If the test asserts stage transitions, update the assertion to `set_registered_model_alias` calls in the same step as the code change.

- [ ] **Step 2: Replace stage transitions with aliases**

In `src/hbacc_prj/training.py`, replace lines 314-324 (the two `transition_model_version_stage` blocks) with:

```python
        client.set_registered_model_alias(
            name=model_names[0], alias="staging", version=primary_version
        )
        for alias_name in model_names[1:]:
            alias_version = mlflow.register_model(model_info.model_uri, alias_name)
            registered_versions[alias_name] = str(alias_version.version)
            client.set_registered_model_alias(
                name=alias_name, alias="staging", version=str(alias_version.version)
            )
```

- [ ] **Step 3: Write `scripts/bootstrap_model_aliases.py`**

```python
"""One-off: give each dataset model a @production alias if it has none.

Assigns @production to the newest registered version of {dataset}-forecaster
for every datasets/*.yaml. Safe to re-run (skips models that already have it).
Env: MLFLOW_TRACKING_URI, optional DATASETS_DIR (default: datasets).
"""
from __future__ import annotations

import os
from pathlib import Path

from mlflow.tracking import MlflowClient

from hbacc_prj.dataset_config import load_all_dataset_configs


def main() -> None:
    tracking_uri = os.environ["MLFLOW_TRACKING_URI"]
    client = MlflowClient(tracking_uri=tracking_uri)
    directory = Path(os.environ.get("DATASETS_DIR", "datasets"))
    for config in load_all_dataset_configs(directory):
        model_name = f"{config.name}-forecaster"
        versions = client.search_model_versions(f"name = '{model_name}'")
        if not versions:
            print(f"{model_name}: no versions registered — skip")
            continue
        if any("production" in (v.aliases or []) for v in versions):
            print(f"{model_name}: @production already set — skip")
            continue
        newest = max(versions, key=lambda v: int(v.version))
        client.set_registered_model_alias(model_name, "production", newest.version)
        print(f"{model_name}: @production -> v{newest.version}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run training tests**

Run: `pytest tests/test_sprint_04_training.py -v`
Expected: pass (fix assertions if they referenced stages)

- [ ] **Step 5: Commit**

```bash
git add src/hbacc_prj/training.py scripts/bootstrap_model_aliases.py tests/test_sprint_04_training.py
git commit -m "feat(training): register new versions via @staging alias (MLflow 3)"
```

---

### Task 9: Frontend auth — token plumbing, login, gating

**Files:**
- Create: `frontend/src/lib/auth.ts`
- Modify: `frontend/src/lib/api.ts` (token header + 401 handling + login/me functions; replace all bare `fetch` calls with `apiFetch`)
- Modify: `frontend/src/pages/LoginPage.tsx` (real form)
- Modify: `frontend/src/App.tsx` (auth gate)
- Modify: `frontend/src/components/layout/Sidebar.tsx` (user chip + logout)

**Interfaces:**
- Consumes: `POST /api/api/v1/auth/login`, `GET /api/api/v1/auth/me` (browser paths — proxy strips first `/api`).
- Produces: `getToken()/setToken()/clearToken()` in `lib/auth.ts`; `apiFetch(input, init?) -> Promise<Response>` in `api.ts`; `AuthUser { username: string; role: 'dev' | 'manager' }`; `login(username, password) -> Promise<AuthUser>`; window event `dealight:logout` fired on 401. App passes `user: AuthUser` down; Sidebar gains `user` + `onLogout` props (Task 10 adds pages + `pendingCount`).

- [ ] **Step 1: Create `frontend/src/lib/auth.ts`**

```ts
const TOKEN_KEY = 'dealight.token'

export function getToken(): string | null {
  return window.localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  window.localStorage.removeItem(TOKEN_KEY)
}
```

- [ ] **Step 2: Add `apiFetch` + auth API to `frontend/src/lib/api.ts`**

At the top (after imports):

```ts
import { clearToken, getToken, setToken } from '@/lib/auth'

export async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const token = getToken()
  const headers = new Headers(init?.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(input, { ...init, headers })
  if (res.status === 401) {
    clearToken()
    window.dispatchEvent(new Event('dealight:logout'))
  }
  return res
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export interface AuthUser {
  username: string
  role: 'dev' | 'manager'
}

export async function login(username: string, password: string): Promise<AuthUser> {
  const res = await fetch('/api/api/v1/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!res.ok) {
    throw new Error(res.status === 401 ? 'Invalid username or password' : `HTTP ${res.status}`)
  }
  const body = await res.json()
  setToken(body.access_token)
  return { username: body.username, role: body.role }
}

export function fetchMe(): Promise<AuthUser> {
  return getJson('/api/api/v1/auth/me')
}
```

Then replace **every** bare `fetch(` call in this file with `apiFetch(` — except inside `login` (runs pre-token). Call sites: `streamChat`, `submitApproval`, `uploadPredictCsv`, `fetchPredictJob`, `getJson`, `fetchDatasetSummary`, `fetchLatestRun`, `fetchTopSkus`, `fetchSummary`, `fetchMonitoringLatest`, `listDriftReports`, `triggerRetrain`, `fetchRetrainRun`, `uploadIngestCsv`.

- [ ] **Step 3: Rewrite `LoginPage.tsx` as a real form**

Keep the existing visual shell (dot-grid background, emerald glow, card, logo, title). Replace the FEATURES list + CTA button with a form; the component becomes:

```tsx
import { useState } from 'react'
import { TrendingUp, ArrowRight, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { login, type AuthUser } from '@/lib/api'

interface LoginPageProps {
  onLogin: (user: AuthUser) => void
}

export default function LoginPage({ onLogin }: LoginPageProps) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      onLogin(await login(username, password))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen bg-zinc-950 flex items-center justify-center relative overflow-hidden">
      <div
        className="absolute inset-0 opacity-[0.025]"
        style={{
          backgroundImage: 'radial-gradient(circle, #fff 1px, transparent 1px)',
          backgroundSize: '28px 28px',
        }}
      />
      <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
        <div className="h-[700px] w-[700px] rounded-full bg-emerald-700/15 blur-[140px]" />
      </div>
      <div className="relative z-10 w-full max-w-sm mx-4">
        <div className="bg-zinc-900/80 backdrop-blur border border-zinc-800 rounded-2xl p-8 shadow-2xl shadow-black/60">
          <div className="flex justify-center mb-6">
            <div className="h-16 w-16 rounded-2xl bg-emerald-600/20 border border-emerald-500/40 flex items-center justify-center shadow-lg shadow-emerald-900/30">
              <TrendingUp className="h-8 w-8 text-emerald-400" />
            </div>
          </div>
          <div className="text-center mb-8">
            <h1 className="text-2xl font-bold text-zinc-50 tracking-tight">Dealight Analytics</h1>
            <p className="text-zinc-400 text-sm mt-1.5 leading-relaxed">
              AI-powered retail sales & forecasting assistant
            </p>
          </div>
          <form onSubmit={submit} className="space-y-3">
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Username"
              autoComplete="username"
              className="h-10 w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 text-sm text-zinc-100 outline-none focus:border-emerald-500"
            />
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Password"
              autoComplete="current-password"
              className="h-10 w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 text-sm text-zinc-100 outline-none focus:border-emerald-500"
            />
            {error && <p className="text-xs text-red-400">{error}</p>}
            <Button
              type="submit"
              disabled={busy || !username || !password}
              className="w-full bg-emerald-600 hover:bg-emerald-500 text-white"
              size="lg"
            >
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : (
                <>Sign in <ArrowRight className="h-4 w-4" /></>
              )}
            </Button>
          </form>
          <p className="text-center text-xs text-zinc-600 mt-4">
            Powered by DuckDB · LightGBM · LLM
          </p>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Gate `App.tsx`**

Add auth state and render LoginPage when logged out:

```tsx
import LoginPage from './pages/LoginPage'
import { fetchMe, type AuthUser } from '@/lib/api'
import { clearToken, getToken } from '@/lib/auth'

// inside App():
const [user, setUser] = useState<AuthUser | null>(null)
const [authChecked, setAuthChecked] = useState(false)

useEffect(() => {
  if (!getToken()) {
    setAuthChecked(true)
    return
  }
  fetchMe()
    .then(setUser)
    .catch(() => clearToken())
    .finally(() => setAuthChecked(true))
}, [])

useEffect(() => {
  const onLogout = () => setUser(null)
  window.addEventListener('dealight:logout', onLogout)
  return () => window.removeEventListener('dealight:logout', onLogout)
}, [])

const handleLogout = () => {
  clearToken()
  setUser(null)
}

if (!authChecked) return null
if (!user) return <LoginPage onLogin={setUser} />
```

Guard the `fetchDatasets` effect: add `if (!user) return` at its top and `user` to its dependency array. Pass `user={user}` and `onLogout={handleLogout}` to `Sidebar`.

- [ ] **Step 5: Sidebar user chip + logout**

Extend `Props` in `Sidebar.tsx`:

```ts
import type { AuthUser, DatasetConfig } from '@/lib/api'
import { LogOut } from 'lucide-react'
// in Props:
  user: AuthUser
  onLogout: () => void
```

Replace the footer div (`Backend: FastAPI · Agent: OpenRouter`) with:

```tsx
      <div className="hidden border-t border-zinc-800 p-3 md:block">
        <div className="flex items-center justify-between">
          <div className="flex flex-col leading-tight">
            <span className="text-xs font-medium text-zinc-200">{user.username}</span>
            <span className={`text-[10px] uppercase ${user.role === 'manager' ? 'text-amber-400' : 'text-emerald-400'}`}>
              {user.role}
            </span>
          </div>
          <button
            onClick={onLogout}
            title="Sign out"
            className="rounded-md border border-zinc-800 p-1.5 text-zinc-500 hover:bg-zinc-900 hover:text-zinc-300"
          >
            <LogOut className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
```

- [ ] **Step 6: Typecheck + build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: no type errors, build succeeds

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/auth.ts frontend/src/lib/api.ts frontend/src/pages/LoginPage.tsx \
  frontend/src/App.tsx frontend/src/components/layout/Sidebar.tsx
git commit -m "feat(web): real login with JWT, auth-gated workspace, logout"
```

---

### Task 10: Frontend Models page + Approvals page

**Files:**
- Modify: `frontend/src/lib/api.ts` (models/promotion API functions)
- Create: `frontend/src/components/models/ComparePanel.tsx`
- Create: `frontend/src/pages/ModelsPage.tsx`
- Create: `frontend/src/pages/ApprovalsPage.tsx`
- Modify: `frontend/src/components/layout/Sidebar.tsx` (nav items, manager gating, pending badge)
- Modify: `frontend/src/App.tsx` (routes)
- Modify: `frontend/src/pages/PipelinePage.tsx` (link hint to Models)

**Interfaces:**
- Consumes: Task 6 endpoints via browser paths `/api/api/v1/models/...` and `/api/api/v1/promotion-requests...`; `AuthUser` + `apiFetch` from Task 9.
- Produces: `WorkspacePage` union extended with `'models' | 'approvals'`; api.ts exports `ModelVersionItem`, `ModelVersions`, `ModelCompare`, `PromotionRequest`, `fetchModelVersions(dataset)`, `fetchModelCompare(dataset, candidate?)`, `createPromotionRequest(dataset, version, note?)`, `listPromotionRequests(status?)`, `reviewPromotionRequest(id, action, comment?)`.

- [ ] **Step 1: Add API functions to `frontend/src/lib/api.ts`**

```ts
// ---------------------------------------------------------------------------
// Models & promotion workflow
// ---------------------------------------------------------------------------

export interface ModelVersionItem {
  version: string
  run_id: string | null
  created_at: number
  aliases: string[]
  metrics: Record<string, number>
}

export interface ModelVersions {
  dataset: string
  model_name: string
  versions: ModelVersionItem[]
}

export interface ModelCompare {
  dataset: string
  model_name: string
  candidate: ModelVersionItem
  production: ModelVersionItem | null
}

export interface PromotionRequest {
  id: number
  dataset: string
  model_name: string
  candidate_version: string
  current_prod_version: string | null
  requested_by: string
  request_note: string | null
  status: 'pending' | 'approved' | 'rejected'
  reviewed_by: string | null
  review_comment: string | null
  created_at: string
  reviewed_at: string | null
}

export function fetchModelVersions(dataset: string): Promise<ModelVersions> {
  return getJson(`${DATASET_API}/models/${encodeURIComponent(dataset)}/versions`)
}

export async function fetchModelCompare(
  dataset: string,
  candidate?: string,
): Promise<ModelCompare | null> {
  const suffix = candidate ? `?candidate=${encodeURIComponent(candidate)}` : ''
  const res = await apiFetch(
    `${DATASET_API}/models/${encodeURIComponent(dataset)}/compare${suffix}`,
  )
  if (res.status === 404) return null
  if (!res.ok) throw new Error(await res.text() || `HTTP ${res.status}`)
  return res.json()
}

export async function createPromotionRequest(
  dataset: string,
  candidateVersion: string,
  note?: string,
): Promise<PromotionRequest> {
  const res = await apiFetch(
    `${DATASET_API}/models/${encodeURIComponent(dataset)}/promotion-requests`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ candidate_version: candidateVersion, note: note ?? null }),
    },
  )
  if (!res.ok) throw new Error(await res.text() || `HTTP ${res.status}`)
  return res.json()
}

export function listPromotionRequests(status?: string): Promise<{ items: PromotionRequest[] }> {
  const suffix = status ? `?status=${encodeURIComponent(status)}` : ''
  return getJson(`${DATASET_API}/promotion-requests${suffix}`)
}

export async function reviewPromotionRequest(
  id: number,
  action: 'approve' | 'reject',
  comment?: string,
): Promise<PromotionRequest> {
  const res = await apiFetch(`${DATASET_API}/promotion-requests/${id}/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ comment: comment ?? null }),
  })
  if (!res.ok) throw new Error(await res.text() || `HTTP ${res.status}`)
  return res.json()
}
```

- [ ] **Step 2: Create `frontend/src/components/models/ComparePanel.tsx`**

Reusable metric comparison (lower is better for all four metrics):

```tsx
import type { ModelCompare } from '@/lib/api'

const METRICS: { key: string; label: string }[] = [
  { key: 'lightgbm_wape', label: 'WAPE' },
  { key: 'lightgbm_mae', label: 'MAE' },
  { key: 'lightgbm_rmse', label: 'RMSE' },
  { key: 'lightgbm_smape', label: 'sMAPE' },
]

export function ComparePanel({ compare }: { compare: ModelCompare }) {
  const { candidate, production } = compare
  return (
    <div className="overflow-x-auto rounded-lg border border-zinc-800">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-zinc-800 text-left text-xs uppercase text-zinc-500">
            <th className="px-3 py-2">Metric</th>
            <th className="px-3 py-2">Production {production ? `v${production.version}` : '—'}</th>
            <th className="px-3 py-2">Candidate v{candidate.version}</th>
            <th className="px-3 py-2">Δ</th>
          </tr>
        </thead>
        <tbody>
          {METRICS.map(({ key, label }) => {
            const prod = production?.metrics[key]
            const cand = candidate.metrics[key]
            const delta = prod !== undefined && cand !== undefined ? cand - prod : null
            const better = delta !== null && delta < 0
            return (
              <tr key={key} className="border-b border-zinc-800/60 last:border-0">
                <td className="px-3 py-2 font-medium text-zinc-300">{label}</td>
                <td className="px-3 py-2 text-zinc-400">{prod?.toFixed(4) ?? '—'}</td>
                <td className="px-3 py-2 text-zinc-100">{cand?.toFixed(4) ?? '—'}</td>
                <td className={`px-3 py-2 font-medium ${
                  delta === null ? 'text-zinc-600' : better ? 'text-emerald-400' : 'text-red-400'
                }`}>
                  {delta === null ? '—' : `${delta > 0 ? '+' : ''}${delta.toFixed(4)}`}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 3: Create `frontend/src/pages/ModelsPage.tsx`**

```tsx
import { useCallback, useEffect, useState } from 'react'
import { GitCompareArrows, Rocket } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ComparePanel } from '@/components/models/ComparePanel'
import {
  createPromotionRequest,
  fetchModelCompare,
  fetchModelVersions,
  listPromotionRequests,
  type ModelCompare,
  type ModelVersions,
  type PromotionRequest,
} from '@/lib/api'

const STATUS_STYLE: Record<PromotionRequest['status'], string> = {
  pending: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
  approved: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  rejected: 'bg-red-500/15 text-red-300 border-red-500/30',
}

export default function ModelsPage({ dataset }: { dataset: string }) {
  const [versions, setVersions] = useState<ModelVersions | null>(null)
  const [compare, setCompare] = useState<ModelCompare | null>(null)
  const [requests, setRequests] = useState<PromotionRequest[]>([])
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const reload = useCallback(() => {
    setError(null)
    fetchModelVersions(dataset).then(setVersions).catch((e: Error) => setError(e.message))
    fetchModelCompare(dataset).then(setCompare).catch(() => setCompare(null))
    listPromotionRequests()
      .then(({ items }) => setRequests(items.filter((r) => r.dataset === dataset)))
      .catch(() => setRequests([]))
  }, [dataset])

  useEffect(reload, [reload])

  const requestPromote = async () => {
    if (!compare) return
    setBusy(true)
    setError(null)
    try {
      await createPromotionRequest(dataset, compare.candidate.version, note || undefined)
      setNote('')
      reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed')
    } finally {
      setBusy(false)
    }
  }

  const candidateIsProd = compare?.production?.version === compare?.candidate.version

  return (
    <div className="h-full overflow-y-auto p-4 md:p-6">
      <div className="mx-auto max-w-4xl space-y-6">
        <div>
          <h1 className="text-lg font-semibold text-zinc-100">Models</h1>
          <p className="text-sm text-zinc-500">
            {versions?.model_name ?? `${dataset}-forecaster`} — registry versions & promotion
          </p>
        </div>

        {error && (
          <div className="rounded-md border border-red-800 bg-red-950/40 p-3 text-sm text-red-300">
            {error}
          </div>
        )}

        <section className="space-y-2">
          <h2 className="flex items-center gap-2 text-sm font-medium text-zinc-300">
            <GitCompareArrows className="h-4 w-4 text-emerald-400" /> Staging vs Production
          </h2>
          {compare ? (
            <>
              <ComparePanel compare={compare} />
              <div className="flex items-center gap-2">
                <input
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="Note for the reviewer (optional)"
                  className="h-9 flex-1 rounded-md border border-zinc-700 bg-zinc-900 px-3 text-sm text-zinc-100 outline-none focus:border-emerald-500"
                />
                <Button
                  onClick={requestPromote}
                  disabled={busy || candidateIsProd}
                  className="bg-emerald-600 hover:bg-emerald-500 text-white"
                >
                  <Rocket className="h-4 w-4" /> Request promote
                </Button>
              </div>
            </>
          ) : (
            <p className="text-sm text-zinc-500">
              No staging candidate yet — trigger a retrain from the Drift page first.
            </p>
          )}
        </section>

        <section className="space-y-2">
          <h2 className="text-sm font-medium text-zinc-300">Versions</h2>
          <div className="overflow-x-auto rounded-lg border border-zinc-800">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-zinc-800 text-left text-xs uppercase text-zinc-500">
                  <th className="px-3 py-2">Version</th>
                  <th className="px-3 py-2">Alias</th>
                  <th className="px-3 py-2">WAPE</th>
                  <th className="px-3 py-2">MAE</th>
                  <th className="px-3 py-2">Created</th>
                </tr>
              </thead>
              <tbody>
                {(versions?.versions ?? []).map((v) => (
                  <tr key={v.version} className="border-b border-zinc-800/60 last:border-0">
                    <td className="px-3 py-2 font-medium text-zinc-100">v{v.version}</td>
                    <td className="px-3 py-2">
                      {v.aliases.map((a) => (
                        <span
                          key={a}
                          className={`mr-1 rounded border px-1.5 py-0.5 text-[10px] uppercase ${
                            a === 'production'
                              ? 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300'
                              : 'border-amber-500/30 bg-amber-500/15 text-amber-300'
                          }`}
                        >
                          {a}
                        </span>
                      ))}
                    </td>
                    <td className="px-3 py-2 text-zinc-300">
                      {v.metrics.lightgbm_wape?.toFixed(4) ?? '—'}
                    </td>
                    <td className="px-3 py-2 text-zinc-300">
                      {v.metrics.lightgbm_mae?.toFixed(4) ?? '—'}
                    </td>
                    <td className="px-3 py-2 text-zinc-500">
                      {new Date(v.created_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
                {!versions?.versions.length && (
                  <tr>
                    <td colSpan={5} className="px-3 py-4 text-center text-zinc-500">
                      No registered versions
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="space-y-2">
          <h2 className="text-sm font-medium text-zinc-300">Promotion requests</h2>
          <div className="space-y-2">
            {requests.map((r) => (
              <div
                key={r.id}
                className="flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-900/50 px-3 py-2 text-sm"
              >
                <div className="flex flex-col">
                  <span className="text-zinc-200">
                    v{r.candidate_version}
                    {r.current_prod_version ? ` (replacing v${r.current_prod_version})` : ''}
                  </span>
                  <span className="text-xs text-zinc-500">
                    by {r.requested_by} · {new Date(r.created_at).toLocaleString()}
                    {r.review_comment ? ` · "${r.review_comment}"` : ''}
                  </span>
                </div>
                <span className={`rounded border px-2 py-0.5 text-[10px] uppercase ${STATUS_STYLE[r.status]}`}>
                  {r.status}
                </span>
              </div>
            ))}
            {!requests.length && <p className="text-sm text-zinc-500">No requests yet.</p>}
          </div>
        </section>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Create `frontend/src/pages/ApprovalsPage.tsx`**

```tsx
import { useCallback, useEffect, useState } from 'react'
import { Check, ChevronDown, ChevronRight, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ComparePanel } from '@/components/models/ComparePanel'
import {
  fetchModelCompare,
  listPromotionRequests,
  reviewPromotionRequest,
  type ModelCompare,
  type PromotionRequest,
} from '@/lib/api'

export default function ApprovalsPage({ onDecided }: { onDecided: () => void }) {
  const [pending, setPending] = useState<PromotionRequest[]>([])
  const [history, setHistory] = useState<PromotionRequest[]>([])
  const [openId, setOpenId] = useState<number | null>(null)
  const [compare, setCompare] = useState<ModelCompare | null>(null)
  const [comment, setComment] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const reload = useCallback(() => {
    listPromotionRequests()
      .then(({ items }) => {
        setPending(items.filter((r) => r.status === 'pending'))
        setHistory(items.filter((r) => r.status !== 'pending'))
      })
      .catch((e: Error) => setError(e.message))
  }, [])

  useEffect(reload, [reload])

  const toggle = (r: PromotionRequest) => {
    if (openId === r.id) {
      setOpenId(null)
      return
    }
    setOpenId(r.id)
    setCompare(null)
    fetchModelCompare(r.dataset, r.candidate_version).then(setCompare).catch(() => setCompare(null))
  }

  const decide = async (id: number, action: 'approve' | 'reject') => {
    setBusy(true)
    setError(null)
    try {
      await reviewPromotionRequest(id, action, comment || undefined)
      setComment('')
      setOpenId(null)
      reload()
      onDecided()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Review failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="h-full overflow-y-auto p-4 md:p-6">
      <div className="mx-auto max-w-4xl space-y-6">
        <div>
          <h1 className="text-lg font-semibold text-zinc-100">Approvals</h1>
          <p className="text-sm text-zinc-500">Model promotion requests awaiting review</p>
        </div>

        {error && (
          <div className="rounded-md border border-red-800 bg-red-950/40 p-3 text-sm text-red-300">
            {error}
          </div>
        )}

        <section className="space-y-2">
          {pending.map((r) => (
            <div key={r.id} className="rounded-lg border border-zinc-800 bg-zinc-900/50">
              <button
                onClick={() => toggle(r)}
                className="flex w-full items-center justify-between px-3 py-2.5 text-left text-sm"
              >
                <div className="flex flex-col">
                  <span className="text-zinc-100">
                    {r.dataset}: v{r.candidate_version}
                    {r.current_prod_version ? ` → replaces v${r.current_prod_version}` : ' → first production'}
                  </span>
                  <span className="text-xs text-zinc-500">
                    by {r.requested_by} · {new Date(r.created_at).toLocaleString()}
                    {r.request_note ? ` · "${r.request_note}"` : ''}
                  </span>
                </div>
                {openId === r.id ? (
                  <ChevronDown className="h-4 w-4 text-zinc-500" />
                ) : (
                  <ChevronRight className="h-4 w-4 text-zinc-500" />
                )}
              </button>
              {openId === r.id && (
                <div className="space-y-3 border-t border-zinc-800 p-3">
                  {compare ? (
                    <ComparePanel compare={compare} />
                  ) : (
                    <p className="text-sm text-zinc-500">Loading live comparison…</p>
                  )}
                  <div className="flex items-center gap-2">
                    <input
                      value={comment}
                      onChange={(e) => setComment(e.target.value)}
                      placeholder="Comment (optional)"
                      className="h-9 flex-1 rounded-md border border-zinc-700 bg-zinc-900 px-3 text-sm text-zinc-100 outline-none focus:border-emerald-500"
                    />
                    <Button
                      onClick={() => decide(r.id, 'approve')}
                      disabled={busy}
                      className="bg-emerald-600 hover:bg-emerald-500 text-white"
                    >
                      <Check className="h-4 w-4" /> Approve
                    </Button>
                    <Button
                      onClick={() => decide(r.id, 'reject')}
                      disabled={busy}
                      className="bg-red-700 hover:bg-red-600 text-white"
                    >
                      <X className="h-4 w-4" /> Reject
                    </Button>
                  </div>
                </div>
              )}
            </div>
          ))}
          {!pending.length && <p className="text-sm text-zinc-500">No pending requests.</p>}
        </section>

        <section className="space-y-2">
          <h2 className="text-sm font-medium text-zinc-300">History</h2>
          {history.map((r) => (
            <div
              key={r.id}
              className="flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-900/30 px-3 py-2 text-sm"
            >
              <span className="text-zinc-300">
                {r.dataset}: v{r.candidate_version} · by {r.requested_by}
              </span>
              <span className="text-xs text-zinc-500">
                {r.status} by {r.reviewed_by}
                {r.review_comment ? ` — "${r.review_comment}"` : ''}
              </span>
            </div>
          ))}
          {!history.length && <p className="text-sm text-zinc-500">Nothing reviewed yet.</p>}
        </section>
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Wire pages + gating in `Sidebar.tsx` and `App.tsx`**

`Sidebar.tsx`:
- `export type WorkspacePage = 'dashboard' | 'predict' | 'pipeline' | 'drift' | 'chat' | 'models' | 'approvals'`
- Import `Boxes`, `BadgeCheck` from lucide-react. Add to `ITEMS` (after `drift`): `{ id: 'models', label: 'Models', Icon: Boxes, hint: 'Registry versions & promotion' }`.
- Add props `pendingCount: number` alongside `user`/`onLogout`. After the `ITEMS.map` loop inside `<nav>`, render the manager-only item:

```tsx
        {user.role === 'manager' && (
          <button
            onClick={() => onChange('approvals')}
            className={`group flex min-w-fit items-start gap-2 rounded-md border px-2.5 py-2 text-left transition-colors md:w-full md:gap-3 md:px-3 md:py-2.5 ${
              current === 'approvals'
                ? 'border-emerald-500/30 bg-emerald-600/15 text-emerald-200'
                : 'border-transparent text-zinc-300 hover:bg-zinc-900/80'
            }`}
          >
            <BadgeCheck className={`h-4 w-4 mt-0.5 flex-shrink-0 ${current === 'approvals' ? 'text-emerald-300' : 'text-zinc-500 group-hover:text-zinc-300'}`} />
            <div className="flex flex-1 flex-col leading-tight">
              <span className="flex items-center gap-2 text-sm font-medium">
                Approvals
                {pendingCount > 0 && (
                  <span className="rounded-full bg-amber-500/20 px-1.5 text-[10px] font-semibold text-amber-300">
                    {pendingCount}
                  </span>
                )}
              </span>
              <span className="hidden text-[11px] text-zinc-500 md:block">Promotion requests</span>
            </div>
          </button>
        )}
```

`App.tsx`:
- `WORKSPACE_PAGES` set gains `'models'`, `'approvals'`.
- Pending count state (import `useCallback`, `listPromotionRequests`):

```tsx
const [pendingCount, setPendingCount] = useState(0)
const refreshPending = useCallback(() => {
  if (user?.role !== 'manager') return
  listPromotionRequests('pending')
    .then(({ items }) => setPendingCount(items.length))
    .catch(() => setPendingCount(0))
}, [user])
useEffect(refreshPending, [refreshPending])
```

- Redirect non-managers away from approvals:

```tsx
useEffect(() => {
  if (user && user.role !== 'manager' && page === 'approvals') setPage('dashboard')
}, [user, page])
```

- Render (inside `<main>`):

```tsx
        {page === 'models' && <ModelsPage dataset={datasetName} />}
        {page === 'approvals' && user.role === 'manager' && (
          <ApprovalsPage onDecided={refreshPending} />
        )}
```

- Pass `pendingCount={pendingCount}` to `Sidebar`.

- [ ] **Step 6: PipelinePage hint**

In `frontend/src/pages/PipelinePage.tsx`, find the train/retrain run status display (search for `train`), and add a small link near it (adjust to local JSX context; if no train status section exists, put it in the page header):

```tsx
<button
  onClick={() => { const u = new URL(window.location.href); u.searchParams.set('page', 'models'); window.location.href = u.toString() }}
  className="text-xs text-emerald-400 underline hover:text-emerald-300"
>
  View results in Models →
</button>
```

- [ ] **Step 7: Typecheck + build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: clean

- [ ] **Step 8: Commit**

```bash
git add frontend/src
git commit -m "feat(web): Models page with compare/promote + manager Approvals page"
```

---

### Task 11: Local config, docs, full test pass

**Files:**
- Modify: `infra/docker-compose.yml` (api service env: `JWT_SECRET`)
- Modify: `docs/GKE_DEPLOY_RUNBOOK.md` (auth + promotion ops section)

**Interfaces:**
- Consumes: everything above.
- Produces: documented ops procedure Task 12 follows verbatim.

- [ ] **Step 1: docker-compose env**

In `infra/docker-compose.yml`, in the forecast-api service `environment:` block (near `MLFLOW_MODEL_URI`, line ~204), add:

```yaml
      JWT_SECRET: ${JWT_SECRET:-dev-insecure-secret}
```

- [ ] **Step 2: Runbook section**

Append to `docs/GKE_DEPLOY_RUNBOOK.md`:

```markdown
## Sprint 09 — auth + model promotion

One-time setup after deploying the sprint-09 image:

# 1. Add secrets (generate a strong JWT secret + user passwords)
kubectl -n dealight patch secret platform-secrets --type merge -p "{\"stringData\":{
  \"JWT_SECRET\": \"$(openssl rand -hex 32)\",
  \"SEED_DEV_PASSWORD\": \"<choose>\",
  \"SEED_MANAGER_PASSWORD\": \"<choose>\"
}}"
kubectl -n dealight rollout restart deploy/forecast-api

# 2. Apply the schema migration (same pattern as sprint 07)
POD=$(kubectl -n dealight get pod -l app=forecast-api -o jsonpath='{.items[0].metadata.name}')
kubectl -n dealight cp scripts/sprint_09_auth_promotion_schema.sql $POD:/tmp/s09.sql
kubectl -n dealight exec $POD -- python -c "import os,psycopg; c=psycopg.connect(os.environ['DATABASE_URL']); c.execute(open('/tmp/s09.sql').read()); c.commit()"

# 3. Seed users (reads SEED_* + DATABASE_URL from the pod env)
kubectl -n dealight exec $POD -- python scripts/seed_users.py

# 4. Bootstrap @production aliases (once)
kubectl -n dealight exec $POD -- python scripts/bootstrap_model_aliases.py

Smoke checklist:
- Login as dev1 and manager1 (http://136.68.214.220) — dev has no Approvals page.
- Models page shows versions of {dataset}-forecaster with alias badges.
- Dev requests promote on the @staging candidate; manager approves in Approvals.
- Next Predict CSV run uses the new version (check forecast-api logs for
  "Loading production model models:/...@production").
- Dev calling POST /api/v1/promotion-requests/{id}/approve directly gets 403.
```

- [ ] **Step 3: Full backend + frontend pass**

Run: `pytest tests/ -q --ignore=tests/load && cd frontend && npx tsc --noEmit && npm run build`
Expected: all green

- [ ] **Step 4: Commit**

```bash
git add infra/docker-compose.yml docs/GKE_DEPLOY_RUNBOOK.md
git commit -m "docs(ops): sprint-09 auth + promotion setup in runbook and compose"
```

---

### Task 12: Deploy to GKE + end-to-end verification

**Files:** none new (operations task; follows the runbook section from Task 11)

**Interfaces:**
- Consumes: the full committed feature + `docs/GKE_DEPLOY_RUNBOOK.md` Sprint 09 section; existing CD loop (push to main → CI on `dealight-gke` runners → Kaniko build → cd.yml bump `[skip ci]` → ArgoCD sync).

- [ ] **Step 1: Push and watch CI/CD**

```bash
git push origin main
gh run list --limit 3   # then gh run watch <id> --exit-status
```

Expected: ci.yml green (build+tests), Kaniko image build, then a `ci(cd): deploy <sha> [skip ci]` bump commit lands.

- [ ] **Step 2: Wait for ArgoCD sync + pod rollout**

```bash
kubectl -n dealight get pods
```

Expected: forecast-api + web restart onto new images; all pods Ready; ArgoCD app Synced/Healthy.

- [ ] **Step 3: Run the one-time ops steps**

Execute runbook Sprint 09 steps 1-4 (secrets patch, schema migration, seed users, alias bootstrap) exactly as written in Task 11.

- [ ] **Step 4: API smoke from the workstation**

```bash
BASE=http://136.68.214.220
TOKEN=$(curl -s $BASE/api/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"manager1","password":"<seeded>"}' | python -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
curl -s $BASE/api/api/v1/auth/me -H "Authorization: Bearer $TOKEN"
curl -s $BASE/api/api/v1/models/hbaac_sku/versions -H "Authorization: Bearer $TOKEN" | head -c 400
curl -s -o /dev/null -w '%{http_code}\n' $BASE/api/forecast-runs/latest   # expect 401
curl -s -o /dev/null -w '%{http_code}\n' $BASE/api/health                 # probes fine
```

Expected: me returns manager role; versions lists registry entries; unauthenticated business route returns 401.

- [ ] **Step 5: Browser smoke (Playwright MCP or manual)**

Walk the runbook checklist: login both roles, dev sees no Approvals, full request → approve → predict flow, logout. Verify the forecast-api log line `Loading production model models:/hbaac_sku-forecaster@production` after the first post-approval predict.

- [ ] **Step 6: Update sprint docs + push**

Mark the feature delivered in `docs/sprints/STATUS.md` (follow the file's existing format), commit:

```bash
git add docs/sprints/STATUS.md
git commit -m "docs(status): sprint-09 dev/manager spaces + promotion approval live"
git push origin main
```

---

## Self-Review Notes

- Spec §1-§8 each map to tasks: auth (§1 → T2-T4), aliases (§2 → T8), workflow (§3 → T1, T5, T6), hot reload + predict dataset (§4 → T7), frontend (§5 → T9, T10), error handling (§6 → embedded in T5-T7 code), testing (§7 → per-task TDD + T11/T12 smoke), deployment (§8 → T11, T12).
- Integration test from spec §7 (docker-compose, real MLflow) is realized as the GKE end-to-end verification in Task 12 — same assertions (train → staging → request → approve → predict on new version) against the real stack.
- Naming checked: `require_user`/`require_manager`, `ModelRegistryClient.list_versions/compare/promote/get_alias_version`, `PromotionStore.create_request/has_pending/list_requests/get/mark_reviewed`, `ModelCache.get/invalidate`, browser paths `/api/api/v1/...` used consistently across tasks.
