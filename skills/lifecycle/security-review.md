---
name: security-review
triggers:
  - auth
  - authentication
  - authorization
  - login
  - password
  - token
  - secret
  - credential
  - session
  - payment
  - api
  - database
  - deploy
  - production
description: Check security-sensitive code for common, concrete risk classes before finishing. Triggered for authentication, authorization, secrets, user input, APIs, payments, persistence, deployment, or production-facing changes.
---

Before finishing work that touches any of the above, check for:

- **Secret exposure** — a key, token, password, or credential committed to
  source, logged, or returned in an API response that shouldn't include it.
- **Injection risks** — user input concatenated into a SQL query, shell
  command, or template instead of parameterized/escaped.
- **Authentication and authorization** — every protected action actually
  checks *who* is calling and *what they're allowed to do*, not just that
  a token is present.
- **Unsafe deserialization** — untrusted input passed to something that
  executes it (`eval`, `pickle.loads`, unrestricted YAML load, etc.).
- **Sensitive data leakage** — PII, credentials, or internal error detail
  exposed in a response, log, or error message a user can see.
- **Insecure defaults** — a new config, flag, or permission that defaults
  to permissive/open rather than restrictive.
- **Dependency and configuration risks** — a newly added dependency with a
  known issue, or a config change that weakens an existing protection
  (CORS, TLS, auth middleware).

Report anything you find plainly rather than fixing it silently and moving
on — a security-relevant tradeoff deserves to be visible.
