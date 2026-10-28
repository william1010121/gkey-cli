"use strict";
const test = require("node:test");
const assert = require("node:assert");
const { needsVenv } = require("../bin/gkey.js");

test("stdlib-only commands skip venv bootstrap", () => {
  for (const a of [["--help"], ["--list"], ["info", "pdf2zh"], ["--status"], ["-p", "1234", "--stop"], []]) {
    assert.strictEqual(needsVenv(a), false, a.join(" "));
  }
});

test("running a tool needs the venv", () => {
  assert.strictEqual(needsVenv(["pdf2zh", "a.pdf"]), true);
  assert.strictEqual(needsVenv(["-m", "gemini-4-argon-medium", "pdf2zh", "a.pdf"]), true);
  assert.strictEqual(needsVenv(["--pool-size", "8", "mods", "hi"]), true);
});

test("--bench needs the venv and skips its numeric values", () => {
  assert.strictEqual(needsVenv(["--bench", "--concurrency", "8", "--requests", "20"]), true);
  assert.strictEqual(needsVenv(["--bench-real"]), true);
});

test("speed options skip their values when finding the command", () => {
  assert.strictEqual(needsVenv(["--fast", "--help"]), false);
  assert.strictEqual(needsVenv(["--parallel-files", "3", "--batch", "8", "--retries", "2", "pdf2zh", "a.pdf"]), true);
  assert.strictEqual(needsVenv(["--cache", "--status"]), false);
  assert.strictEqual(needsVenv(["--bench", "--dup-pct", "50", "--compare", "pool=4"]), true);
});
