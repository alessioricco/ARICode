---
name: documentation-and-operational-readiness
triggers:
  - api
  - config
  - configuration
  - setup
  - install
  - deploy
  - cli
  - endpoint
description: Keep documentation and setup/config instructions in sync with what actually changed. Triggered when behavior, APIs, setup, configuration, or deployment changes.
---

The harness already requires every task to leave a README with concrete
run instructions (see its own policy) — this skill covers keeping the
*rest* of a project's documentation honest when you change something that
affects it:

- **Update README or other project documentation** wherever it describes
  behavior you just changed, not only the baseline setup instructions.
- **Installation and run instructions** — if you added a dependency,
  changed a command, or introduced a new setup step, reflect it exactly,
  not approximately.
- **Configuration reference** — a new or changed config value/flag/env var
  needs its own entry: name, meaning, default, and where it applies.
- **API examples** — if a request/response shape, endpoint, or CLI flag
  changed, update the examples that show it, not just the prose.
- **Migration notes** — if the change breaks an existing workflow or
  config, say what changes for someone upgrading, not just what's new.

Skip this for an internal-only change with no user-visible behavior, setup,
or config difference.
