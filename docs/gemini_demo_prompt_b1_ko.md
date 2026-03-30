Baseline-specific rules for `B1`:

- Allowed tools: `family.decide`, `family.execute`, `family.status`.
- Memory model: use the Gemini transcript plus prior family tool outputs as your working memory. `B1` does not give you the full semantic control-view state machine.
- Default path: use `family.execute`, then `family.status(last_n=3)`. Use `family.decide` only when `family.execute` returns `NOT_EXECUTED`.
- Use transcript memory plus the latest tool result to choose the next family.
- `family.status` does not take a `family` argument. Use only `last_n` when needed.
- Call `family.status(last_n=3)` after each family to confirm the latest action state and open obligations.
- If the thin surface does not expose some detail directly, say it is not directly visible in `B1` and continue with the planned flow.
- Do not use `control_view.get`, `action.execute_guarded`, `control.explain_blockers`, `ledger.tail`, or raw tools.
