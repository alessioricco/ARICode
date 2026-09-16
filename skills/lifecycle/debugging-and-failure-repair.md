---
name: debugging-and-failure-repair
triggers:
  - error
  - fail
  - failing
  - failed
  - traceback
  - exception
  - crash
  - broken
description: Root-cause and fix a real failure using its actual output. Triggered when tests, builds, lint, type checks, or runtime commands fail.
---

When a test, build, lint, type check, or runtime command fails:

- **Use the real failure output** — the exact error message, stack trace,
  or line number the tool gave you — not a guess at what probably went
  wrong.
- **Locate the root cause** before editing. The line named in a traceback
  is often a symptom; find where the actual wrong value or wrong logic
  originates — and consider that the wrong logic can be in the *test's own
  assumption*, not only the implementation. If a test's captured output
  shows the code behaving one way but the assertion expects something that
  contradicts it, check which one is actually correct before assuming the
  implementation is at fault (see `testing-and-verification` for how to
  confirm this).
- **If a targeted fix doesn't resolve the failure, don't repeat the same
  kind of edit again without changing your diagnosis first.** Two edits to
  the same code path that both leave the identical failure unexplained is
  a sign your root-cause theory is wrong, not that the fix needs another
  attempt — go back and re-read the actual failure output and state trace
  before editing a third time.
- **Make a minimal, targeted fix** at that exact location. A full-file
  rewrite risks losing correct code and introducing a new bug alongside
  the one you're fixing.
- **Rerun the same failing check** after the fix and confirm it actually
  passes now — don't infer success from the edit alone.
- **Avoid blind retries.** Re-running the identical failing check without
  changing anything, hoping for a different result, wastes a cycle and
  tells you nothing new — change something first.
- **Avoid broad rewrites** as a substitute for finding the actual cause —
  if you can't explain why a change fixes the failure, keep looking
  instead of shipping it.
