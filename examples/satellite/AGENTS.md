# Satellite Example Local Instructions

This file supplements the repository-root `AGENTS.md` for work under
`examples/satellite/`.

Keep the existing prototype boundaries, architecture constraints, and
communication rules from the root `AGENTS.md`. The additional guidance here is
meant to keep Codex work segmented, visible, and easy to recover if a session
is interrupted.

## Codex 工作分段规则

- Avoid long stretches of autonomous work. Break work into small segments
  instead of trying to carry a large task through one long uninterrupted pass.
- For any task that is more than trivial, post a short plan before editing
  code. The plan should say which files will be checked, which files are
  expected to be modified, and what is explicitly out of scope for the current
  pass.
- After each completed sub-step, post a short checkpoint in the conversation.
  The checkpoint should say what was finished, which files were changed, and
  what the next step is.
- If the task turns out to be more complex than expected, or if the current
  plan needs to change, pause and explain that before expanding the scope of
  edits.
- Checkpoints do not require a git commit for every step. Commits still follow
  the user's request. The point is to make intermediate reasoning, progress,
  and partial results visible and recoverable.
- Do not leave important design judgments only in internal reasoning. Any
  decision that affects later implementation direction should be stated
  clearly in the reply or written down in the relevant doc.
- Keep work small and diffs small. Solve only the currently requested problem
  in each pass.
