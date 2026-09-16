---
name: implementation-planning
triggers:
  - refactor
  - architecture
  - redesign
  - restructure
  - rearchitect
description: Plan a multi-file or architectural change before touching code. Triggered for multi-file, architectural, or otherwise non-trivial changes.
---

For a change that touches more than one or two files, or changes how
existing pieces fit together:

- **Inspect the existing code first.** Read the code you're about to change
  and its callers/callees before writing anything — don't plan against your
  assumption of what the code does.
- **Identify the controlling code path.** Find the one place the behavior
  actually flows through, not every place that looks related — a plan
  built around the wrong path produces a fix that doesn't actually apply.
- **Make the smallest coherent plan** that satisfies the requirements —
  an ordered list of the files you'll touch and why, not a rewrite of
  everything nearby.
- **Avoid unrelated refactors.** Don't rename, reformat, or "clean up"
  code the task didn't ask you to touch, even if you'd do it differently —
  that's a separate task, and it makes this change harder to review.
- **Update the plan when evidence changes.** If exploring the code reveals
  your plan's assumption was wrong, revise the plan before continuing —
  don't keep implementing against a plan you already know is stale.

Skip this for a small, contained change — a plan for a one-file fix is
overhead, not help.
