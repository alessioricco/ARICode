---
name: requirements-analysis
triggers:
  - feature
  - requirement
  - bug
  - unclear
  - ambiguous
  - vague
description: Pin down what a feature request or bug fix actually requires before writing code. Triggered for feature requests, bug fixes, and vague or underspecified tasks.
---

No user is available to answer follow-up questions (see the harness's own
autonomy policy) — that makes it your job to make the requirements
concrete yourself, in your own reasoning, before you start editing:

- **Requested outcome** — what should actually be true when this is done,
  stated concretely, not just restated from the task text.
- **Acceptance criteria** — specific, checkable conditions you (or someone
  else) could verify without asking you what you meant.
- **Constraints and assumptions** — anything the task doesn't say
  explicitly that you're filling in yourself (a framework choice, a data
  format, a scope boundary). Note these in your final message so they're
  visible, not silently baked in.
- **Edge cases** — inputs, states, or conditions the happy path doesn't
  cover that a real implementation still has to handle correctly.
- **What "verified" means here** — which of the requested outcome's parts
  are actually checkable (a test, a build, a manual run) before you call
  the task done, vs. which are unverifiable claims you should avoid making.

For a genuinely small, unambiguous change (a one-line fix, a typo), skip
this — it isn't worth the overhead.
