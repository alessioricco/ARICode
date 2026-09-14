Verify an OpenHands SDK API before relying on it: $ARGUMENTS

The OpenHands SDK is young and its API surface changes. Do not guess symbol names,
import paths, or signatures.

1. Look up the symbol/topic in $ARGUMENTS against the live sources:
   - https://docs.openhands.dev/sdk/getting-started
   - https://docs.openhands.dev/sdk/guides/custom-tools
   - Canonical example:
     https://github.com/OpenHands/software-agent-sdk/blob/main/examples/01_standalone_sdk/02_custom_tools.py
   - Repo examples index: https://github.com/OpenHands/software-agent-sdk
2. Confirm the exact import path, class/function name, and signature.
3. Report the confirmed usage as a short code snippet, and note the source URL.
4. If the symbol no longer exists or was renamed, give the current equivalent.

Do not modify project files in this command — just verify and report.
