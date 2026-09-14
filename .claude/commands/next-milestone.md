Work on the next milestone of this project.

1. Read @docs/SPEC.md and find the milestone list (section 11). Identify the
   lowest-numbered milestone that is not yet fully implemented in the repo.
   If $ARGUMENTS names a specific milestone number, do that one instead.
2. Before writing code that touches the OpenHands SDK, confirm any SDK symbol you
   are unsure about against the live docs/examples (see @CLAUDE.md "SDK references").
   Prefer `get_default_tools()` over importing tool classes by name.
3. Implement the milestone. Keep to the file layout in the spec (section 5).
4. Write or update the matching tests. Run `pytest -q` and fix failures.
5. Do NOT break the model-agnostic invariant: no hardcoded model/provider/base URL
   in `src/`. Switching provider must remain a `.env`-only change.
6. Report concisely: what you implemented, test results, and what the next
   milestone is. Do not start the next milestone without being asked.
