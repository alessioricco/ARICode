---
name: repository-discovery
triggers:
  - implement
  - build
  - add
  - create
  - refactor
  - migrate
  - integrate
  - scaffold
  - inspect
  - explore
description: Establish an unfamiliar repository's actual stack and conventions before editing it. Triggered for substantial coding tasks and project inspection.
---

Before making a substantial change, spend a few tool calls establishing facts
instead of assuming — **do not assume Python, Node, or any particular
project layout** just because a previous task used one:

- **Languages and frameworks** — read whatever manifest(s) are actually
  present (`pyproject.toml`, `package.json`, `go.mod`, `Cargo.toml`,
  `pom.xml`/`build.gradle`, or others); don't guess from habit.
- **Package manager** — the lockfile actually committed (`uv.lock`/
  `poetry.lock`, `package-lock.json`/`pnpm-lock.yaml`/`yarn.lock`, `go.sum`,
  `Cargo.lock`, ...), not whichever you'd default to.
- **Entry points** — how the project is actually run, built, or imported:
  check the README, a `Makefile`, `package.json` `scripts`, or the
  language's own `main`/`cmd` convention.
- **Existing verification commands** — the project's own test, build, lint,
  format, and type-check commands, from its README, CI config
  (`.github/workflows/`), or manifest scripts — not a default you're used
  to reaching for.
- **Configuration and conventions** — env var patterns, existing code
  style, directory layout, and any `AGENTS.md`/`CONTRIBUTING.md` already
  present in the project.

When you can't find an explicit command or convention for something, say so
rather than inventing one.
