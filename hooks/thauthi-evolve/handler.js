// thauthi-evolve: opt-in session-boundary hook. See HOOK.md for the contract.
import { execFile } from "node:child_process";
import { statSync } from "node:fs";
import { isAbsolute } from "node:path";

const EVENTS = new Set(["command:new", "command:reset", "session:auto-reset"]);
const MIN_INTERVAL_MS = 60_000;
const TIMEOUT_MS = 5_000;

let running = false;
let lastRunAt = 0;

function debug(reason) {
  if (process.env.THAUTHI_DEBUG) {
    console.log(`[thauthi-evolve] skipped: ${reason}`);
  }
}

function workspaceOf(event) {
  const dir = event?.context?.workspaceDir;
  if (typeof dir !== "string" || !isAbsolute(dir) || dir.includes("\0")) {
    return null;
  }
  try {
    return statSync(dir).isDirectory() ? dir : null;
  } catch {
    return null;
  }
}

export default async function handler(event) {
  try {
    const key = `${event?.type}:${event?.action}`;
    if (!EVENTS.has(key)) {
      return;
    }
    if (running || Date.now() - lastRunAt < MIN_INTERVAL_MS) {
      debug("rate limited");
      return;
    }
    const cwd = workspaceOf(event);
    if (!cwd) {
      debug("no usable workspaceDir");
      return;
    }
    running = true;
    lastRunAt = Date.now();
    const env = {
      PATH: process.env.PATH ?? "",
      HOME: process.env.HOME ?? "",
      LANG: process.env.LANG ?? "C.UTF-8",
    };
    if (process.env.THAUTHI_DEBUG) {
      env.THAUTHI_DEBUG = process.env.THAUTHI_DEBUG;
    }
    await new Promise((resolve) => {
      // Fixed argv, no shell, empty stdin: never forwards the transcript or any event data.
      const child = execFile(
        "thauthi",
        ["hook", "stop"],
        { cwd, env, timeout: TIMEOUT_MS, maxBuffer: 64 * 1024, windowsHide: true },
        (err) => {
          if (err) {
            debug(`thauthi exited with error (${err.code ?? err.signal ?? "unknown"})`);
          }
          resolve();
        },
      );
      child.stdin?.end();
    });
  } catch {
    debug("unexpected error");
  } finally {
    running = false;
  }
}
