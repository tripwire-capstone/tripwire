import { test } from "node:test";
import assert from "node:assert/strict";
import { checkLaminarKey, checkOpenAiKey, projectIdFrom } from "./keys.js";
import type { FetchFn } from "../types/tripwire.js";

// A fake fetch that answers with `status` and remembers what it was asked
const fakeFetch = (status: number): { fetchFn: FetchFn; calls: { url: string; init?: RequestInit }[] } => {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fetchFn = (async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    return new Response("{}", { status });
  }) as FetchFn;
  return { fetchFn, calls };
};

const failingFetch = (async () => {
  throw new Error("getaddrinfo ENOTFOUND");
}) as FetchFn;

test("checkOpenAiKey accepts a key OpenAI answers 200 for", async () => {
  const fake = fakeFetch(200);
  assert.deepEqual(await checkOpenAiKey("sk-good", fake.fetchFn), { ok: true });
  assert.equal(fake.calls[0].url, "https://api.openai.com/v1/models");
  assert.deepEqual(fake.calls[0].init?.headers, { Authorization: "Bearer sk-good" });
});

test("checkOpenAiKey rejects a key OpenAI answers 401 for", async () => {
  const result = await checkOpenAiKey("sk-bad", fakeFetch(401).fetchFn);
  assert.equal(result.ok, false);
  assert.match(result.ok ? "" : result.reason, /isn't valid/);
});

test("checkOpenAiKey reports when OpenAI can't be reached", async () => {
  const result = await checkOpenAiKey("sk-any", failingFetch);
  assert.equal(result.ok, false);
  assert.match(result.ok ? "" : result.reason, /Couldn't reach OpenAI.*ENOTFOUND/);
});

test("checkLaminarKey runs SELECT 1 through Laminar's SQL API", async () => {
  const fake = fakeFetch(200);
  assert.deepEqual(await checkLaminarKey("https://laminar.example.com/", "lmnr-key", fake.fetchFn), { ok: true });
  assert.equal(fake.calls[0].url, "https://laminar.example.com/v1/sql/query");
  assert.equal(fake.calls[0].init?.method, "POST");
  assert.deepEqual(JSON.parse(String(fake.calls[0].init?.body)), { query: "SELECT 1", parameters: {} });
});

test("checkLaminarKey rejects a key Laminar refuses", async () => {
  for (const status of [401, 403]) {
    const result = await checkLaminarKey("https://laminar.example.com", "bad", fakeFetch(status).fetchFn);
    assert.equal(result.ok, false);
    assert.match(result.ok ? "" : result.reason, /project API key/);
  }
});

test("checkLaminarKey reports other answers with their status", async () => {
  const result = await checkLaminarKey("https://laminar.example.com", "key", fakeFetch(502).fetchFn);
  assert.equal(result.ok ? "" : result.reason, "Laminar answered 502 when checking the key.");
});

test("projectIdFrom finds the ID in a Laminar URL or on its own", () => {
  const id = "42fc1e36-6c0e-4a69-9850-b0e849bb4656";
  assert.equal(projectIdFrom(`https://laminar.example.com/project/${id}/traces?pastHours=24`), id);
  assert.equal(projectIdFrom(id.toUpperCase()), id);
  assert.equal(projectIdFrom("not a project"), null);
});
