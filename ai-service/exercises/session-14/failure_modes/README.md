# Session 14 — failure-mode reproductions

The three mistakes that show up most often when you first wire a supervisor with a human
gate. Each module is a **minimal, self-contained reproduction**: it exhibits the symptom
by default, and the fix is a one-line `str_replace` you apply live.

| # | Module | Symptom | Fix on screen |
|---|--------|---------|---------------|
| 1 | `routing_no_converge.py` | The supervisor ping-pongs between two agents; only the step budget stops it, and `routing_history` ends with a `limit` row. | Restore the legality guard: `... and not _already_ran(target, history)`. |
| 2 | `state_clobber.py` | Two parallel writes to the same channel: one silently disappears (or LangGraph raises "can receive only one value per step"). | Give the channel a reducer: `Annotated[list, operator.add]`. |
| 3 | `interrupt_no_resume.py` | `interrupt()` pauses, but the resume starts a *new* run and the paused one stays stuck. | Use the same `thread_id` on start and resume: `f"s14:{estimation_id}"`. |

Each is pinned by `tests/domain/graph/supervisor/test_failure_modes.py`, which asserts
both the broken behaviour and the fixed one, so the "before/after" is reproducible.
