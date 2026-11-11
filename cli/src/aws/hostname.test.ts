import { test } from "node:test";
import assert from "node:assert/strict";
import { hostnameProblem, isInZone, normalizeHostname, suggestHostname } from "./hostname.js";

test("normalizeHostname trims, lowercases and drops a trailing dot", () => {
  assert.equal(normalizeHostname("  Laminar.Example.COM. "), "laminar.example.com");
});

test("suggestHostname puts laminar in front of the zone", () => {
  assert.equal(suggestHostname("example.com."), "laminar.example.com");
});

test("isInZone accepts the zone itself and names under it, not look-alikes", () => {
  assert.equal(isInZone("laminar.example.com", "example.com"), true);
  assert.equal(isInZone("example.com", "example.com"), true);
  assert.equal(isInZone("laminar.notexample.com", "example.com"), false);
});

test("hostnameProblem accepts a valid address, with or without a zone", () => {
  assert.equal(hostnameProblem("laminar.example.com"), undefined);
  assert.equal(hostnameProblem("Laminar.Example.com", "example.com"), undefined);
});

test("hostnameProblem explains each kind of bad answer", () => {
  assert.match(hostnameProblem("") ?? "", /Enter an address/);
  assert.match(hostnameProblem(undefined) ?? "", /Enter an address/);
  assert.match(hostnameProblem("https://laminar.example.com") ?? "", /Leave out https:\/\//);
  assert.match(hostnameProblem("laminar.example.com/app") ?? "", /without a path/);
  assert.match(hostnameProblem("laminar") ?? "", /Use a host name like/);
});

test("hostnameProblem requires the address to be in the zone, and suggests one", () => {
  const problem = hostnameProblem("laminar.other.org", "example.com") ?? "";
  assert.match(problem, /must end in example\.com/);
  assert.match(problem, /laminar\.example\.com/);
});
