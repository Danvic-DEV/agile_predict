# Copilot Instructions: Fail-Closed Policy

## Core Rule
- Never add fallback behavior that hides failures.
- If a required dependency, feed, model output, or data contract fails, surface an explicit error.

## Forbidden Patterns
- Silent fallback to stale, synthetic, placeholder, or historical data when operational data is required.
- Automatic substitution of one source for another without an explicit operator-controlled mode switch.
- Returning success responses when core pipeline stages failed.
- Converting hard failures into warnings unless the feature is explicitly marked non-critical.

## Required Behavior
- Fail closed for customer-facing outputs.
- Return clear error codes/messages that identify the failed stage and reason.
- Preserve diagnostics with explicit failure state (do not mask with "healthy" or "pass").
- Prefer no output over misleading output.
- If a non-critical fallback is truly required, it must be:
  - Explicitly requested by maintainer in the task,
  - Guarded by a named configuration flag,
  - Clearly labeled in API/UI as degraded/fallback mode,
  - Logged with structured context.

## PR / Code Change Checklist
- Does this change introduce any fallback that could hide failure?
- If yes, reject or convert to explicit fail-closed behavior.
- Are error paths visible in API responses and diagnostics?
- Is customer-facing data blocked when upstream integrity is invalid?
