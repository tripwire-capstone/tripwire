import { test } from "node:test";
import assert from "node:assert/strict";
import { UserError } from "./errors.js";
import { hasUser, normalizeEmail, withoutUser, withUser } from "./users.js";

test("normalizeEmail trims and lowercases", () => {
  assert.equal(normalizeEmail("  Ana@Example.COM "), "ana@example.com");
});

test("normalizeEmail rejects anything that isn't an email", () => {
  assert.throws(() => normalizeEmail("ana"), UserError);
  assert.throws(() => normalizeEmail("ana@example"), UserError);
  assert.throws(() => normalizeEmail("a b@example.com"), UserError);
});

test("hasUser ignores case in saved emails", () => {
  assert.equal(hasUser(["Ana@example.com"], "ana@example.com"), true);
  assert.equal(hasUser(["ana@example.com"], "bo@example.com"), false);
});

test("withUser appends a new email and leaves a known one alone", () => {
  assert.deepEqual(withUser(["ana@example.com"], "bo@example.com"), ["ana@example.com", "bo@example.com"]);
  assert.deepEqual(withUser(["ana@example.com"], "ana@example.com"), ["ana@example.com"]);
});

test("withoutUser removes the email, whatever its saved case", () => {
  assert.deepEqual(withoutUser(["Ana@example.com", "bo@example.com"], "ana@example.com"), ["bo@example.com"]);
});

test("withoutUser refuses an unknown email and the last user", () => {
  assert.throws(() => withoutUser(["ana@example.com", "bo@example.com"], "cy@example.com"), /isn't a user/);
  assert.throws(() => withoutUser(["ana@example.com"], "ana@example.com"), /only user/);
});