# Dev/Manager Spaces + Model Promotion Approval — Design

Date: 2026-07-15
Status: Approved (all sections approved in session; user pre-approved spec review)

## Problem

The web UI has no authentication (`LoginPage.tsx` is dead code — `main.tsx`
renders the workspace directly) and no model governance. After a retrain,
`training.py` registers `{dataset}-forecaster` versions and moves them to the
deprecated MLflow *stage* "Staging", but the predict path loads a model once at
startup from a static `MLFLOW_MODEL_URI` env var (pickle fallback) — promoting
a model has no effect on serving.

Goal: split the UI into a **dev space** and a **manager space**. After retrain,
a dev compares the new model version against the current production version on
MLflow metrics and, if better, **requests promotion** staging→production. A
manager has every dev capability **plus approval** of promotion requests.
Approved promotions take effect on subsequent predicts immediately; the old
production model is demoted to staging.

Out of scope: user management UI, OAuth/SSO, login rate limiting, multi-replica
cache coherence (single forecast-api replica today; extension noted in §4).

## Decisions (approved in brainstorming)

1. **Auth**: seeded users + JWT (not role-picker, not OAuth).
2. **UI**: one workspace, role-gated pages (not two separate workspaces).
3. **Serving**: MLflow aliases + in-process hot reload (not TTL cache, not
   env-pin + pod restart).
4. **Request state**: Postgres table (not MLflow tags, not GitOps PRs).
5. Multiple pending requests per dataset allowed; only exact duplicates
   `(dataset, candidate_version)` with status pending are blocked.
6. Reject comment is optional. Managers may create and self-approve requests.

## 1. Auth & roles

Backend (`api/app/`):
- Table `users`: `id, username UNIQUE, password_hash (bcrypt), role
  ('dev'|'manager'), created_at`. Seed `dev1` and `manager1`; passwords come
  from env/secret at seed time, never from the repo.
- New router `auth.py`:
  - `POST /api/v1/auth/login` — username/password → JWT (HS256, secret from
    env `JWT_SECRET`, TTL 12h, claims `sub` + `role`).
  - `GET /api/v1/auth/me` — current identity + role.
- `deps.py` gains `require_user` and `require_manager` dependencies reading
  `Authorization: Bearer`. `require_user` is applied to all existing routers
  (predict, retrain, datasets, drift, ingest, chat); `require_manager` only to
  approve/reject endpoints.
- K8s: new secret `jwt-secret` + seed passwords secret (existing secret
  pattern); docker-compose gets matching env vars for local dev.

Frontend:
- `LoginPage.tsx` becomes a real username/password form calling login and
  storing the JWT in `localStorage`.
- `App.tsx`: no valid token → `LoginPage`; valid token → workspace. A logout
  action clears the token.
- `lib/api.ts` attaches the Bearer header to every request; any 401 clears the
  token and returns to login.
- Role from `/auth/me` lives in a React context and drives sidebar visibility
  (§5); the backend remains the enforcement point.

## 2. Model registry: stages → aliases

- `training.py` stops calling `transition_model_version_stage` (deprecated
  API). After training, when the registration rule passes, the new version of
  `{dataset}-forecaster` gets alias **`@staging`**.
- Alias **`@production`** marks the version predict serves. MLflow moves an
  alias atomically when reassigned (one version per alias name).
- On approval: candidate gets `@production`; the previous production version
  gets `@staging` (the requested "demote to staging" semantics), overwriting
  whatever held `@staging`.
- Bootstrap: a small one-off script (documented in the runbook) assigns
  `@production` to the currently-served version of `hbaac_sku-forecaster`.

## 3. Promotion workflow (backend)

Table `promotion_requests` (migration SQL following the `scripts/init_db.sql`
pattern):

```
id BIGSERIAL PK
dataset TEXT NOT NULL
model_name TEXT NOT NULL
candidate_version TEXT NOT NULL
current_prod_version TEXT NULL        -- null if no @production yet
metrics_snapshot JSONB NOT NULL       -- WAPE/MAE/RMSE/SMAPE of both versions at request time
requested_by TEXT NOT NULL
request_note TEXT NULL
status TEXT NOT NULL DEFAULT 'pending'  -- pending|approved|rejected
reviewed_by TEXT NULL
review_comment TEXT NULL
created_at / reviewed_at TIMESTAMPTZ
```

New router `models.py`:
- `GET /api/v1/models/{dataset}/versions` — versions of
  `{dataset}-forecaster` with aliases and run metrics (from MLflow).
- `GET /api/v1/models/{dataset}/compare?candidate=N` — candidate vs current
  `@production` metric table. This is the dev's post-retrain screen.
- `POST /api/v1/models/{dataset}/promotion-requests` (any authenticated user)
  — creates a request; 409 if candidate already is production or an identical
  pending `(dataset, candidate_version)` exists. Multiple pending requests per
  dataset are otherwise allowed; `metrics_snapshot` is point-in-time, and the
  approval UI always fetches a live comparison.
- `GET /api/v1/promotion-requests?status=...` — visible to all users (devs
  track their requests).
- `POST /api/v1/promotion-requests/{id}/approve` and `/reject` (**manager
  only**). Approve: flip aliases (§2), invalidate the model cache (§4), mark
  row approved. Reject: record status + optional comment. Other pending
  requests for the dataset remain pending.

## 4. Serving hot reload

- `mlflow_loader.py` becomes a per-dataset model cache: `get_model(dataset)`
  loads `models:/{dataset}-forecaster@production` on first use and keeps it in
  RAM with the loaded version. Replaces the single `app.state.model`.
- The approve endpoint (same FastAPI process) calls `cache.invalidate(dataset)`
  directly — the next predict request loads the new version. No Redis pub/sub
  needed at one replica; if scaled out later, add a 60s alias-version check
  (extension note only, not built now).
- **Load-then-swap**: on reload, the old model keeps serving until the new one
  loads successfully; a load failure logs + writes a monitoring event
  (sprint-07 tables) and predict continues on the old model.
- Fallback unchanged: no `@production` alias yet → try `MLFLOW_MODEL_URI` env,
  then pickle, as today. The first promotion of a dataset activates the alias
  path.
- `/predict/csv` is currently dataset-agnostic; it gains an optional `dataset`
  field (default `hbaac_sku`, keeping the deployed behavior) and `PredictPage`
  passes the sidebar's selected dataset, so predictions use that dataset's
  `@production` model.

## 5. Frontend

New page **Models** (all roles, follows the sidebar dataset selector):
- Version table for `{dataset}-forecaster`: version, trained-at,
  WAPE/MAE/RMSE/SMAPE, alias badges (`production` green / `staging` amber).
- **Compare panel**: candidate (default `@staging`) vs `@production`, each
  metric with a green/red delta.
- **Request promote** button (optional note) when candidate ≠ production;
  below it, the dataset's request list with statuses.

New page **Approvals** (manager only in sidebar, with a pending-count badge):
- Pending requests across datasets; each expands to a live comparison (same
  component as the compare panel, live data — not the snapshot) with
  **Approve** / **Reject** (optional comment). Handled items move to a history
  tab.

Flow tie-ins: `PipelinePage` links to Models ("view results") once a train DAG
run finishes. Sidebar hides Approvals for devs, but a dev calling the approve
API directly gets 403.

## 6. Error handling

- **Approve is idempotent, alias-first**: flip MLflow aliases, then write the
  DB row. Alias failure → 502, request stays pending, retryable. DB failure
  after alias flip → retrying approve detects candidate already `@production`
  and only completes the DB record.
- Candidate version deleted in MLflow before approval → 409.
- MLflow unreachable on versions/compare → 502 with a clear message; UI shows
  an error state (existing `datasetError` pattern).
- Expired/invalid JWT → 401 → frontend returns to login.
- Load-then-swap covers bad artifacts at reload (§4).

## 7. Testing

- **Unit (pytest, existing `tests/` pattern)**: login success/failure, expired
  token, dev→approve = 403; request creation + duplicate block; approve flips
  aliases and demotes old prod (MlflowClient mocked); reject; cache
  invalidate→reload; load-then-swap on load failure.
- **Integration (docker-compose, real MLflow)**: small train → new version
  `@staging` → request → approve → next predict job uses the new version
  (asserted via version in response/log).
- **Frontend**: typecheck + build green; manual smoke checklist in the runbook
  (login both roles, dev cannot see Approvals, full request→approve→predict
  flow).

## 8. Deployment

Ships through the existing GitOps loop (CI on ARC runners → Kaniko build →
CD bump → ArgoCD sync): DB migration applied to Cloud SQL (manual exec in the
forecast-api pod, as with prior schema files), new k8s secrets (`jwt-secret`,
seed passwords), alias bootstrap script run once, then smoke per §7.
