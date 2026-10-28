#!/usr/bin/env node
"use strict";
// Thin launcher: finds python3, bootstraps a private venv on first use, then runs the bash CLI.

const { spawnSync, spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const pkgDir = path.resolve(__dirname, "..");
const gkeyHome = process.env.GKEY_HOME || path.join(os.homedir(), ".gkey");
const venvDir = path.join(gkeyHome, "venv");
const venvPython = path.join(venvDir, "bin", "python");

// Commands that only need the Python standard library: no venv bootstrap required.
const STDLIB_ONLY = new Set([
  "-h", "--help", "--list", "--env", "--status", "--stop", "--logs", "--install", "info", "help",
]);

function die(msg) {
  process.stderr.write(`[gkey] ${msg}\n`);
  process.exit(1);
}

function findPython() {
  const candidates = [process.env.GKEY_PYTHON_BIN, "python3", "python"].filter(Boolean);
  for (const c of candidates) {
    const r = spawnSync(c, ["-c", "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"], { stdio: "ignore" });
    if (r.status === 0) return c;
  }
  return null;
}

function needsVenv(args) {
  // Skip leading option/value pairs to find the first real command.
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (STDLIB_ONLY.has(a)) return false;
    if (["-m", "--model", "-b", "--backend", "-e", "--effort", "-p", "--port", "--pool-size"].includes(a)) { i++; continue; }
    if (a.startsWith("-")) continue;
    return true; // a tool name
  }
  return args.length > 0; // bare `gkey` opens the menu (stdlib only)
}

function ensureVenv(python) {
  const req = path.join(pkgDir, "requirements.txt");
  const stamp = path.join(venvDir, ".gkey-stamp");
  const want = crypto.createHash("sha256").update(fs.readFileSync(req)).digest("hex");
  let have = "";
  try { have = fs.readFileSync(stamp, "utf8").trim(); } catch (_) { /* first run */ }
  if (fs.existsSync(venvPython) && have === want) return;

  process.stderr.write("[gkey] First run: setting up a private Python environment in " + venvDir + " ...\n");
  fs.mkdirSync(gkeyHome, { recursive: true });
  if (!fs.existsSync(venvPython)) {
    const v = spawnSync(python, ["-m", "venv", venvDir], { stdio: "inherit" });
    if (v.status !== 0) die("Could not create a virtualenv. On Debian/Ubuntu install python3-venv.");
  }
  const pip = spawnSync(venvPython, ["-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-r", req], { stdio: "inherit" });
  if (pip.status !== 0) die("Dependency install failed (check your network/proxy and retry).");
  if (process.env.GKEY_INSTALL_SDK === "1") {
    const sdk = spawnSync(venvPython, ["-m", "pip", "install", "--quiet", "google-antigravity"], { stdio: "inherit" });
    if (sdk.status !== 0) process.stderr.write("[gkey] Warning: optional google-antigravity SDK could not be installed; the agy backend still works.\n");
  }
  fs.writeFileSync(stamp, want + "\n");
}

function main() {
  if (process.platform === "win32") {
    die("Windows is not supported natively. Please run gkey inside WSL2 (Ubuntu): wsl --install");
  }
  const args = process.argv.slice(2);
  const python = findPython();
  if (!python) die("Python 3.9+ is required but was not found. Install it from https://www.python.org/ or your package manager.");

  const env = { ...process.env, GKEY_HOME: gkeyHome };
  if (needsVenv(args)) {
    ensureVenv(python);
    env.GKEY_PYTHON = venvPython;
  } else if (fs.existsSync(venvPython)) {
    env.GKEY_PYTHON = venvPython;
  } else {
    env.GKEY_PYTHON = env.GKEY_PYTHON || python;
  }

  const child = spawn("bash", [path.join(pkgDir, "gkey"), ...args], { stdio: "inherit", env });
  for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => child.kill(sig));
  child.on("error", (e) => die(e.code === "ENOENT" ? "bash is required but was not found." : String(e)));
  child.on("exit", (code, signal) => {
    if (signal) process.kill(process.pid, signal);
    else process.exit(code === null ? 1 : code);
  });
}

if (require.main === module) main();
module.exports = { needsVenv };
