---
name: fastapi-conventions
triggers:
  - fastapi
  - FastAPI
  - REST API
  - rest api
  - web service
description: Conventions for scaffolding or extending a FastAPI service.
---

When building or extending a FastAPI service:

- Define request/response shapes as Pydantic models, not raw dicts — even for
  a small service, this gives free validation and OpenAPI docs.
- Put route handlers in a router module per resource
  (`routers/users.py`, `routers/health.py`), included into the app in
  `main.py` — don't grow one file with every endpoint.
- Every service needs a `GET /health` endpoint returning 200 with a minimal
  body, for load balancers / orchestrators to probe.
- Use dependency injection (`Depends(...)`) for shared resources (DB
  sessions, auth) instead of importing a global singleton in each handler.
- Return proper HTTP status codes via `HTTPException`, not 200 with an
  `{"error": ...}` body.
