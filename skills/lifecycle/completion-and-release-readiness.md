---
name: completion-and-release-readiness
triggers:
  - release
  - deploy
  - production
  - ship
  - launch
  - "pull request"
  - "PR"
description: Final pre-finish review across requirements, verification, docs, and security — not just "did the tests pass." Triggered before finishing release, deployment, production, PR, or substantial feature work.
---

Before finishing release, deployment, production, PR, or otherwise
substantial work, review — don't just re-state your last message:

- **Acceptance criteria** — check each one from `requirements-analysis`
  against what you actually built, not what you intended to build.
- **Changed behavior** — everything the change actually touches, including
  side effects you didn't set out to cause.
- **Test/build/lint/type-check results** — the real, current output from
  this run, not a memory of an earlier one.
- **Documentation** — is it still accurate for what you shipped (see
  `documentation-and-operational-readiness`)?
- **Security-sensitive changes** — anything that needed a security review
  actually got one.
- **Remaining assumptions and risks** — anything you couldn't verify, or
  had to assume, stated plainly rather than left implicit.

**Never claim the work is done, verified, or ready if required
verification is still failing or was never run.** If a check is
unavailable (no way to run it) or still failing, say that plainly instead
of describing the work as complete.
