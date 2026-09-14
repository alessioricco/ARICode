---
name: pytest-conventions
triggers:
  - pytest
  - test
  - tests
  - unit test
description: Conventions for writing pytest-based tests, triggered when a task mentions testing.
---

When writing or modifying pytest tests:

- Put fixtures and constants at module scope only if shared by multiple
  tests; otherwise keep them local to the test that needs them.
- Prefer plain `assert` statements over `assertEqual`-style helpers.
- Name tests for the behavior they verify (`test_returns_empty_list_when_no_matches`),
  not the method under test (`test_get_matches`).
- Don't mock what you're testing. If a test needs a mock to pass, consider
  whether it's testing the right thing.
- A test that needs a real subprocess/filesystem/network call should say so
  in its name or a short comment — don't hide expensive setup in a fixture
  with an innocuous name.
