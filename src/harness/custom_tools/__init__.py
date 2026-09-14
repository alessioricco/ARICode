"""Custom tools package.

Importing this module registers every real custom tool (each tool module
calls register_tool() at import time as a side effect). That's also what lets
a containerized agent-server register them via
`--import-modules harness.custom_tools` (see docker/agent-server.Dockerfile) —
under HARNESS_EXECUTION=docker the agent loop runs inside the container's own
process, which never otherwise imports this package.

example_tool.py is a template, not a real tool, and is deliberately not
imported here.
"""

from . import run_tests_tool  # noqa: F401
