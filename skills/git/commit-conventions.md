---
name: commit-conventions
triggers:
  - commit
  - git commit
  - pull request
  - "PR"
description: Commit and PR conventions, triggered when a task involves committing or opening a PR.
---

When asked to commit changes or open a pull request:

- Write commit messages that explain *why*, not a restatement of the diff.
- Never commit secrets, `.env` files, or credentials — check `git status`
  output for anything unexpected before staging.
- Keep unrelated changes out of the same commit; if a task touched two
  unrelated things, that's a sign it should have been two tasks.
- Don't push or open a PR unless the task explicitly asked for it.
