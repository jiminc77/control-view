Prompt rules for `B3`:

- Allowed tool: the single semantic control-view step tool.
- Available memory: the latest semantic control-view tool response. Do not rely on transcript-style summaries when the tool response already tells you what to do next.
- Choose the next action only from `state`, `next_action`, `recovery_family`, and `retry_after_ms`.
- `next_action=ADVANCE`: move to the next family in the mission order.
- `next_action=RETRY_SAME_FAMILY`: stay on the same family and retry with the same arguments.
- `next_action=RECOVER_PRECONDITION`: call `recovery_family` first, then return to the blocked family.
- `next_action=STOP`: stop immediately and finish the mission summary.
- Do not inspect raw state, do not ask for extra explanations, and do not guess new arguments.
