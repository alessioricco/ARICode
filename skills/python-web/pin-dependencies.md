---
name: pin-dependencies
paths:
  - "pyproject.toml"
  - "requirements*.txt"
description: Path-triggered rule (not a keyword skill) — fires whenever the agent edits a dependency file.
---

This project's dependency file is being edited. Pin or range-constrain new
dependencies deliberately (`"fastapi>=0.110,<1.0"`, not a bare `"fastapi"`) —
an unconstrained dependency can silently pull in a breaking major version on
the next install.
