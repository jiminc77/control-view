Baseline-specific rules for `B0`:

- Allowed tools: only the raw ROS tools exposed by the attached `ros-mcp-server` profile.
- Memory model: use the Gemini transcript plus prior raw tool outputs as your only working memory. There is no semantic control-view state machine in `B0`.
- Treat raw tool outputs as the source of truth. Infer progress directly from raw ROS state reported in those tool outputs.
- Tool names and schemas depend on the attached raw server profile. Discover and use only the raw inspection, wait, publish, or service tools that are actually exposed.
- Before arming, confirm from raw state that the vehicle is connected, local pose is available, and home is available.
- After takeoff, do one raw state check, then continue.
- For `goto_hold_land` and `goto_rtl`, command the fixed target `(x=1.5, y=0.0, z=3.0)` through the raw tools that the active server exposes.
- After the raw goto command, do not advance until raw state shows `|x-1.5| <= 0.5`, `|y-0.0| <= 0.5`, `|z-3.0| <= 0.7`, and the vehicle is still armed. If not there yet, wait 2 to 4 seconds and check again.
- Only start hold or RTL while the vehicle is still airborne and the target has been reached.
- After landing, do not stop at mode change alone. Keep checking raw state until the vehicle is disarmed or near the ground.
- If takeoff or arm preconditions are not ready, wait and retry the same stage. Do not assume any sidecar semantic shortcut.
