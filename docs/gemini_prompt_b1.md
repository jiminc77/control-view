Prompt rules for `B1`:

- Allowed tools: `family.decide`, `family.execute`, `family.status`.
- Available memory: the Gemini transcript plus prior family tool outputs. `B1` does not expose the full semantic control-view state machine.
- Default family loop:
  - call `family.execute`
  - then call `family.status(last_n=3)` before deciding whether to advance
  - use `family.decide` only when `family.execute` returns `NOT_EXECUTED`
- Advance only when the latest visible action for the current family is `CONFIRMED` and no pending work for that family is visible in `family.status`.
- If the latest visible action is `PENDING`, `NOT_EXECUTED`, `FAILED`, or the current family is still pending, stay on the same family.
- `family.status` does not take a `family` argument. Use only `last_n` when needed.
- If the thin surface does not expose some detail directly, say it is not directly visible in `B1` and continue using only the visible fields.
- Do not use `control_view.get`, `action.execute_guarded`, `control.explain_blockers`, `ledger.tail`, or raw tools.
