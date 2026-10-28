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
