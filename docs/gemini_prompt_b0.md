Prompt rules for `B0`:

- Allowed tools: only the raw ROS tools exposed by the attached `ros-mcp-server` profile.
- Available memory: the Gemini transcript plus prior raw tool outputs only. There is no semantic control-view state machine in `B0`.
- Treat raw tool outputs as the source of truth. Infer progress directly from raw ROS state reported in those tool outputs.
- Tool names and schemas depend on the attached raw server profile. Discover and use only the raw inspection, wait, publish, or service tools that are actually exposed.
- Family completion rules on the raw surface:
  - `ARM`: advance only after raw state shows the vehicle is armed.
  - `TAKEOFF`: advance only after raw state shows altitude near `3.0` and the vehicle is still armed.
  - `GOTO`: advance only after raw state shows `|x-1.5| <= 0.5`, `|y-0.0| <= 0.5`, `|z-3.0| <= 0.7`, and the vehicle is still armed.
  - `HOLD`: after reaching the target, wait briefly and confirm the vehicle remains airborne and stable before advancing.
  - `LAND` or `RTL`: stop only after raw state shows the vehicle is disarmed or near the ground.
- If takeoff or arm preconditions are not ready or not visible yet, wait and retry the same family. Do not assume any sidecar shortcut or hidden state.
