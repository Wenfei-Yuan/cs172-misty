# Known Issues

> Manually maintained log of known issues, workarounds, and fix status.
> Format: add entries as issues are discovered or fixed during development.

---

*(No entries yet — repo is in initial scaffold state.)*

{Screen/VLM startup recovery and boot speech}
a. What was not fixed: Camera/VLM failures, missing boot speech, and screen-search timeout behavior were not fully surfaced or recovered in the runtime pipeline.
b. Last attempt summary: Earlier fixes improved isolated pieces such as camera capture handling or speech logging, but the active runtime still used raw speak calls, swallowed VLM failures, and cached failed screen positions.
c. Why the last fix failed: The previous changes did not reconnect the new capture/audio helpers to the active pipeline, did not preserve real image mime information into VLM requests, and still let boot/search paths treat failure as a usable screen position.
d. Current fix: The runtime now uses structured camera/VLM results, routes speech through audio preparation, logs camera/VLM/speech/search outcomes, and retries screen search without accepting unverified positions.

{Repeated distraction trigger re-arming}
a. What was not fixed: After the first distraction ended, stale stop events could remain in the trigger queue and prevent the next distraction cycle from starting correctly.
b. Last attempt summary: Earlier trigger handling only prevented stop events from causing an immediate false no-response escalation after a distraction cycle.
c. Why the last fix failed: The previous logic still split stop handling between the pipeline and the trigger receiver, re-queued stale stop events while waiting for a new start, and did not safely re-arm cross-cycle trigger state.
d. Current fix: The trigger receiver now drains stale idle stop events, centralizes active interrupt consumption, preserves one pending next-cycle start, and the pipeline now branches on structured distraction outcomes with regression coverage for repeated cycles.
