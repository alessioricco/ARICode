"""Pluggable persistence for server mode's task registry.

`server.py`'s original task registry was a plain in-memory `dict` — fine
for a dev/demo server, a real liability for anything long-running (memory
grows forever; task history is lost on every restart; nothing is shared
across multiple server processes). This module makes that registry a
pluggable `TaskStore`, selected via `HARNESS_TASK_STORE`
(`memory` | `redis` | `sqlite` | `mysql` | `postgres`), so a deployment
that needs persistence or a bounded footprint can opt into one without any
change to `server.py`'s request-handling code — every backend implements
the same small interface.

`memory` (the default) is byte-for-byte the pre-existing behavior: no new
dependency, no persistence across restarts, unbounded unless a TTL is set.
The three SQL backends (`sqlite`/`mysql`/`postgres`) share one
implementation via SQLAlchemy Core, parameterized by dialect — see
`SQLTaskStore` and ROADMAP.md's decisions log for why a shared
implementation was chosen over three hand-written ones. `redis` uses
native per-key expiration for its TTL instead of a purge sweep — see
`RedisTaskStore`.

None of this is agent-facing: it exists purely to answer "what happened to
task X" after the fact, for `GET /tasks/{id}` and the admin CLI
(`harness-admin`, see `admin_cli.py`).
"""

from __future__ import annotations

import copy
import json
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import Config

# Statuses after which a record becomes eligible for TTL expiry — a
# pending/running task is never purged regardless of TTL, since it's still
# actively in use (see TaskStore.purge_expired).
_TERMINAL_STATUSES = ("completed", "failed")


@dataclass
class TaskRecord:
    """One server-mode task's full state — the same shape `server.py`'s
    `_TaskRecord` was before this module existed, now shared with every
    store backend (and given a `project` field it was missing, needed for
    delete-by-project).
    """

    id: str
    task: str
    status: str = "pending"  # pending -> running -> completed | failed
    project: str | None = None
    messages: list[dict] = field(default_factory=list)
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    verification_state: str | None = None
    completion_contract: dict[str, Any] | None = None
    acceptance_results: list[dict[str, Any]] | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> TaskRecord:
        return cls(**data)


class TaskStore(ABC):
    """Common interface every backend implements. A caller (`server.py`,
    `admin_cli.py`) never needs to know which backend is actually active.
    """

    @abstractmethod
    def save(self, record: TaskRecord) -> None:
        """Insert `record`, or replace it if `record.id` already exists
        (upsert) — the only write operation; there's no separate
        "create" vs. "update" since callers already hold the full record
        in memory and just want its current state persisted.
        """

    @abstractmethod
    def get(self, task_id: str) -> TaskRecord | None:
        """Return the record for `task_id`, or `None` if it doesn't exist
        (never existed, or was purged/deleted).
        """

    @abstractmethod
    def delete(self, task_id: str) -> bool:
        """Delete one record. Returns whether it actually existed."""

    @abstractmethod
    def delete_by_project(self, project: str) -> int:
        """Delete every record whose `project` matches exactly. Returns
        how many were deleted — `0` is a normal, valid result (unknown
        project, or a project with no tasks left), not an error.
        """

    @abstractmethod
    def purge_expired(self, ttl_seconds: int) -> int:
        """Delete every *terminal* (`completed`/`failed`) record whose
        `updated_at` is older than `ttl_seconds`. `ttl_seconds <= 0` means
        "keep forever" — always a no-op, never treated as "expire
        immediately". Returns how many were purged.
        """

    def close(self) -> None:
        """Release any held resources (DB connections, etc). A no-op by
        default — only backends that hold real connections need to
        override this.
        """


class MemoryTaskStore(TaskStore):
    """The original behavior, now behind the `TaskStore` interface: an
    in-memory dict, no persistence across restarts, unbounded unless a
    TTL is configured. Thread-safe (server.py drives this from multiple
    threads — the background task thread and the request-handling thread).

    `save()`/`get()` always store/return an independent `copy.deepcopy`,
    never the caller's own object reference. This isn't just defensive:
    every other backend (SQL, Redis) can *only* ever hand back a freshly
    deserialized snapshot of whatever was last `save()`d — there's no way
    for them to return a "live" object a caller could mutate without
    calling `save()` again. Returning a live reference here instead would
    make `MemoryTaskStore` behave subtly differently from every other
    backend in two ways: a concurrent reader could observe a record
    mid-mutation (the background task thread mutates `record`'s fields
    across several lines before its own next `save()` call), and a caller
    could mutate a `get()`-returned record and have that change silently
    "stick" with no explicit `save()` — a bug that would only surface when
    switching to a real backend.
    """

    def __init__(self) -> None:
        self._records: dict[str, TaskRecord] = {}
        self._lock = threading.Lock()

    def save(self, record: TaskRecord) -> None:
        snapshot = copy.deepcopy(record)
        with self._lock:
            self._records[record.id] = snapshot

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            record = self._records.get(task_id)
            return copy.deepcopy(record) if record is not None else None

    def delete(self, task_id: str) -> bool:
        with self._lock:
            return self._records.pop(task_id, None) is not None

    def delete_by_project(self, project: str) -> int:
        with self._lock:
            matching = [tid for tid, r in self._records.items() if r.project == project]
            for tid in matching:
                del self._records[tid]
            return len(matching)

    def purge_expired(self, ttl_seconds: int) -> int:
        if ttl_seconds <= 0:
            return 0
        cutoff = time.time() - ttl_seconds
        with self._lock:
            expired = [
                tid
                for tid, r in self._records.items()
                if r.status in _TERMINAL_STATUSES and r.updated_at < cutoff
            ]
            for tid in expired:
                del self._records[tid]
            return len(expired)


class SQLTaskStore(TaskStore):
    """Shared implementation for `sqlite`/`mysql`/`postgres`, parameterized
    by a SQLAlchemy engine — one `tasks` table, one set of queries, no
    dialect-specific SQL. See ROADMAP.md's decisions log for why one
    implementation was chosen over three.

    `messages`/`completion_contract`/`acceptance_results` are stored as
    JSON text (`json.dumps`/`json.loads`) rather than native JSON columns,
    since native JSON column support and operators differ enough across
    sqlite/mysql/postgres that a plain TEXT column plus explicit
    (de)serialization is the one representation guaranteed to behave
    identically on all three.

    `save()` is implemented as "try UPDATE, INSERT if that touched zero
    rows" rather than a dialect-specific upsert (`INSERT ... ON CONFLICT`
    for postgres/sqlite vs. `ON DUPLICATE KEY UPDATE` for mysql) —
    slightly less efficient (two round-trips on first insert), but the
    same code path works unmodified on all three dialects.
    """

    def __init__(self, url: str) -> None:
        import sqlalchemy as sa

        self._sa = sa
        self._engine = sa.create_engine(url, future=True)
        self._metadata = sa.MetaData()
        self._table = sa.Table(
            "harness_tasks",
            self._metadata,
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("task", sa.Text, nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("project", sa.String(255), nullable=True, index=True),
            sa.Column("messages", sa.Text, nullable=False),
            sa.Column("error", sa.Text, nullable=True),
            sa.Column("created_at", sa.Float, nullable=False),
            sa.Column("updated_at", sa.Float, nullable=False, index=True),
            sa.Column("verification_state", sa.String(64), nullable=True),
            sa.Column("completion_contract", sa.Text, nullable=True),
            sa.Column("acceptance_results", sa.Text, nullable=True),
        )
        self._metadata.create_all(self._engine)

    def _row_to_record(self, row: Any) -> TaskRecord:
        return TaskRecord(
            id=row.id,
            task=row.task,
            status=row.status,
            project=row.project,
            messages=json.loads(row.messages),
            error=row.error,
            created_at=row.created_at,
            updated_at=row.updated_at,
            verification_state=row.verification_state,
            completion_contract=(
                json.loads(row.completion_contract) if row.completion_contract else None
            ),
            acceptance_results=(
                json.loads(row.acceptance_results) if row.acceptance_results else None
            ),
        )

    def _record_to_values(self, record: TaskRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "task": record.task,
            "status": record.status,
            "project": record.project,
            "messages": json.dumps(record.messages),
            "error": record.error,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "verification_state": record.verification_state,
            "completion_contract": (
                json.dumps(record.completion_contract)
                if record.completion_contract is not None
                else None
            ),
            "acceptance_results": (
                json.dumps(record.acceptance_results)
                if record.acceptance_results is not None
                else None
            ),
        }

    def save(self, record: TaskRecord) -> None:
        values = self._record_to_values(record)
        with self._engine.begin() as conn:
            update_values = {k: v for k, v in values.items() if k != "id"}
            result = conn.execute(
                self._table.update().where(self._table.c.id == record.id).values(**update_values)
            )
            if result.rowcount == 0:
                conn.execute(self._table.insert().values(**values))

    def get(self, task_id: str) -> TaskRecord | None:
        with self._engine.connect() as conn:
            row = conn.execute(self._table.select().where(self._table.c.id == task_id)).fetchone()
            return self._row_to_record(row) if row is not None else None

    def delete(self, task_id: str) -> bool:
        with self._engine.begin() as conn:
            result = conn.execute(self._table.delete().where(self._table.c.id == task_id))
            return result.rowcount > 0

    def delete_by_project(self, project: str) -> int:
        with self._engine.begin() as conn:
            result = conn.execute(self._table.delete().where(self._table.c.project == project))
            return result.rowcount

    def purge_expired(self, ttl_seconds: int) -> int:
        if ttl_seconds <= 0:
            return 0
        cutoff = time.time() - ttl_seconds
        with self._engine.begin() as conn:
            result = conn.execute(
                self._table.delete().where(
                    self._table.c.status.in_(_TERMINAL_STATUSES),
                    self._table.c.updated_at < cutoff,
                )
            )
            return result.rowcount

    def close(self) -> None:
        self._engine.dispose()


class RedisTaskStore(TaskStore):
    """Each record is one JSON blob under `harness:task:<id>`, plus a
    Redis SET per project (`harness:project:<project>`) so
    `delete_by_project` doesn't need a full `SCAN` of every key.

    TTL uses Redis's own native per-key expiration instead of a purge
    sweep: a write for a *terminal* (`completed`/`failed`) record sets
    `EX=ttl_seconds` at write time (Redis deletes the key itself, no
    polling needed); a write for a still-`pending`/`running` record is
    written with no expiration (`PERSIST`), matching every other
    backend's "never purge an in-flight task" rule. `purge_expired()` is
    therefore a deliberate no-op here — expiry already happened, or will,
    natively — kept only so the interface stays uniform across backends.

    `ttl_seconds` is fixed at construction (from `cfg.task_ttl_seconds`),
    not a per-`save()` argument — `TaskStore.save()`'s signature must stay
    identical across every backend so `server.py` never needs to know
    which one is active.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        db: int,
        password: str | None,
        use_tls: bool,
        ttl_seconds: int = 0,
    ) -> None:
        import redis as redis_lib

        self._redis = redis_lib.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            ssl=use_tls,
            decode_responses=True,
        )
        self._ttl_seconds = ttl_seconds

    def _task_key(self, task_id: str) -> str:
        return f"harness:task:{task_id}"

    def _project_key(self, project: str) -> str:
        return f"harness:project:{project}"

    def save(self, record: TaskRecord) -> None:
        key = self._task_key(record.id)
        payload = json.dumps(record.to_json_dict())
        pipe = self._redis.pipeline()
        if record.status in _TERMINAL_STATUSES and self._ttl_seconds > 0:
            pipe.set(key, payload, ex=self._ttl_seconds)
        else:
            pipe.set(key, payload)
            pipe.persist(key)
        if record.project:
            pipe.sadd(self._project_key(record.project), record.id)
        pipe.execute()

    def get(self, task_id: str) -> TaskRecord | None:
        raw = self._redis.get(self._task_key(task_id))
        if raw is None:
            return None
        return TaskRecord.from_json_dict(json.loads(raw))

    def delete(self, task_id: str) -> bool:
        record = self.get(task_id)
        deleted = self._redis.delete(self._task_key(task_id)) > 0
        if deleted and record is not None and record.project:
            self._redis.srem(self._project_key(record.project), task_id)
        return deleted

    def delete_by_project(self, project: str) -> int:
        project_key = self._project_key(project)
        task_ids = self._redis.smembers(project_key)
        if not task_ids:
            return 0
        pipe = self._redis.pipeline()
        for task_id in task_ids:
            pipe.delete(self._task_key(task_id))
        pipe.delete(project_key)
        results = pipe.execute()
        # One `delete` result per task_id, then the final project-set delete.
        return sum(1 for r in results[:-1] if r)

    def purge_expired(self, ttl_seconds: int) -> int:
        # Deliberate no-op — see class docstring. Native per-key TTL,
        # set at write time in save(), already handles this.
        return 0

    def close(self) -> None:
        self._redis.close()


def _build_sql_url(cfg: Config) -> str:
    if cfg.task_store == "sqlite":
        return f"sqlite:///{cfg.task_store_sqlite_path}"
    if cfg.task_store == "mysql":
        auth = cfg.task_store_mysql_user
        if cfg.task_store_mysql_password:
            auth += f":{cfg.task_store_mysql_password}"
        return (
            f"mysql+pymysql://{auth}@{cfg.task_store_mysql_host}:"
            f"{cfg.task_store_mysql_port}/{cfg.task_store_mysql_database}"
        )
    if cfg.task_store == "postgres":
        auth = cfg.task_store_postgres_user
        if cfg.task_store_postgres_password:
            auth += f":{cfg.task_store_postgres_password}"
        return (
            f"postgresql+psycopg2://{auth}@{cfg.task_store_postgres_host}:"
            f"{cfg.task_store_postgres_port}/{cfg.task_store_postgres_database}"
        )
    raise ValueError(f"_build_sql_url called with a non-SQL backend: {cfg.task_store!r}")


def build_task_store(cfg: Config) -> TaskStore:
    """Construct the `TaskStore` selected by `cfg.task_store`. The one
    place that maps `HARNESS_TASK_STORE` to a concrete backend — `server.py`
    and `admin_cli.py` both call this instead of importing a specific
    backend class, so adding a future backend means one new branch here.

    Raises a clear `RuntimeError` (not an `ImportError`) naming the exact
    extra to install when the chosen backend's dependency isn't present —
    matches `server.py`'s own existing pattern for the `server` extra.
    """
    if cfg.task_store == "memory":
        return MemoryTaskStore()
    if cfg.task_store == "redis":
        try:
            import redis  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                'HARNESS_TASK_STORE=redis requires the "store-redis" extra: '
                'run `uv pip install -e ".[store-redis]"`.'
            ) from exc
        return RedisTaskStore(
            host=cfg.task_store_redis_host,
            port=cfg.task_store_redis_port,
            db=cfg.task_store_redis_db,
            password=cfg.task_store_redis_password,
            use_tls=cfg.task_store_redis_use_tls,
            ttl_seconds=cfg.task_ttl_seconds,
        )
    if cfg.task_store in ("sqlite", "mysql", "postgres"):
        extra = f"store-{cfg.task_store}"
        try:
            import sqlalchemy  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                f'HARNESS_TASK_STORE={cfg.task_store} requires the "{extra}" extra: '
                f'run `uv pip install -e ".[{extra}]"`.'
            ) from exc
        return SQLTaskStore(_build_sql_url(cfg))
    raise ValueError(f"Unsupported HARNESS_TASK_STORE: {cfg.task_store!r}")
