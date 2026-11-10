---
name: thauthi-evolve
description: "Opt-in: run `thauthi hook stop` (Darwinian gene-pool evolution) at session boundaries"
metadata:
  {
    "openclaw":
      {
        "emoji": "🧠",
        "events": ["command:new", "command:reset", "session:auto-reset"],
        "requires": { "bins": ["thauthi"], "env": ["THAUTHI_OPENCLAW_HOOK"] },
      },
  }
---

# thauthi-evolve

Runs `thauthi hook stop` when a session ends (`/new`, `/reset`, or automatic reset) so the
Thauthi gene pool can evolve between sessions. It is the OpenClaw counterpart of the Claude Code
Stop hook, with a deliberately smaller footprint.

## Off by default

The hook is **not eligible** until you opt in by giving it `THAUTHI_OPENCLAW_HOOK` (any nonblank
value) and the `thauthi` CLI is on `PATH`:

```bash
openclaw hooks enable thauthi-evolve
# then set hooks.internal.entries.thauthi-evolve.env.THAUTHI_OPENCLAW_HOOK = "on"
```

Verify with `openclaw hooks info thauthi-evolve`, then trigger `/new` and check that
`<workspace>/.thauthi/` is updated. `openclaw hooks check` alone does not prove the handler ran.

## What it does

- Executes exactly `thauthi hook stop` (no shell, fixed arguments) in the session's workspace
  directory, with a 5 second timeout and a minimal environment (`PATH`, `HOME`, locale).
- Writes only Thauthi's own state under `<workspace>/.thauthi/` (gene pool).
- Runs at most once per minute and never overlaps itself.

## What it never does

- It does **not** read, copy, or pass the session transcript, message bodies, sender ids, or
  config. Claude Code's Stop hook hands `transcript_path` to Thauthi, which can mine it and forge
  tools. This hook sends an empty stdin, so no transcript mining or tool forging happens.
- It does not call a model, use the network, or modify files outside `<workspace>/.thauthi/`.
- It never throws and never blocks a reset: any failure is swallowed (set `THAUTHI_DEBUG=1` on the
  Gateway to log a one-line reason).

Internal hooks are trusted code that run inside the Gateway process. Review `handler.js` before
enabling.
