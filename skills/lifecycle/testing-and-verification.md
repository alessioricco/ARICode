---
name: testing-and-verification
triggers:
  - test
  - implement
  - fix
  - bug
description: Add and run real tests for new or changed behavior, using the project's own verification commands. Triggered for implementation and bug-fix tasks.
---

- **Discover the project's own verification commands** first (see the
  `repository-discovery` skill) — don't assume `pytest`/`npm test`/any
  other default without checking what this project actually uses.
- **Add focused tests for new behavior** — a test that would fail without
  your change and passes with it, not a broad rewrite of the existing
  suite.
- **Run the project's own tests, build, lint, and type-check commands**
  whenever they're configured for this project — not just the one you
  personally favor.
- **Read the actual command output.** A summary you remember or expect is
  not the same as output you just read.
- **If something fails, repair it and rerun verification** — see the
  `debugging-and-failure-repair` skill for how to root-cause it.
- **Never weaken or delete a test merely to make it pass** — an assertion
  that's too strict is a signal to fix the code, not loosen the test,
  *unless the test's own expectation is provably wrong.* Prove it, don't
  assume it: trace the actual state your code produces (the captured
  output already shows you this) against exactly what the failing
  assertion claims. If the two genuinely disagree — the assertion expects
  something the traced state contradicts — the test is the bug, and the
  fix is to correct the test's expectation, not to keep re-editing code
  that already behaves correctly. Don't re-edit the same working code
  twice without re-checking this.
- **Distinguish "no tests found" from "verified."** A project with no
  tests yet, or a check that couldn't run at all, is not the same as
  confirmed-working — say so plainly rather than treating silence as a
  pass.
