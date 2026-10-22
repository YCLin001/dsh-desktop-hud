/**
 * Host half of the floating HUD.
 *
 * The browser bundle renders the in-window HUD and pushes session snapshots to
 * the desktop overlay's localhost bridge. This entry owns the lifetime of that
 * independent always-on-top window: it starts the Tk overlay together with the
 * DSH Host and stops it when the Host goes away.
 */
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HUD_PORT = "38471";

function overlayScript() {
  const here = path.dirname(fileURLToPath(import.meta.url));
  return path.join(here, "..", "overlay", "hud_overlay.py");
}

function pythonCommand() {
  if (process.env.DSH_HUD_PYTHON) return process.env.DSH_HUD_PYTHON;
  const home = process.env.USERPROFILE || process.env.HOME || "";
  const bundled = home
    ? path.join(home, ".dsh", "dsh-runtimes", "dsh-primary-runtime", "dependencies", "python", "pythonw.exe")
    : "";
  if (bundled && existsSync(bundled)) return bundled;
  return "pythonw.exe";
}

export function apply(ctx) {
  let child = null;
  let stopped = false;

  try {
    const script = overlayScript();
    if (!existsSync(script)) {
      console.warn("[floating-hud] overlay script missing:", script);
      return;
    }

    const args = [script, "--host-pid", String(process.pid), "--port", HUD_PORT];
    const options = {
      detached: true,
      stdio: "ignore",
      windowsHide: true,
      cwd: path.dirname(script),
    };

    const start = (command) => {
      const proc = spawn(command, args, options);
      proc.on("error", () => {
        if (stopped) return;
        if (command !== "python.exe") start("python.exe");
      });
      proc.unref();
      return proc;
    };

    child = start(pythonCommand());

    ctx.effect(() => () => {
      stopped = true;
      try {
        child?.kill();
      } catch {
        /* already exited */
      }
      child = null;
    }, "floating-hud: desktop overlay lifetime");
  } catch (error) {
    console.warn("[floating-hud] desktop overlay unavailable:", error);
  }
}
