import { test } from "node:test";
import assert from "node:assert/strict";
import { BUCKET_RESOURCES, missingResources } from "./bootstrap.js";

test("everything is missing from an empty state", () => {
  assert.deepEqual(missingResources(""), BUCKET_RESOURCES);
});

test("only what the state lacks is imported", () => {
  const stateList = [
    "data.aws_caller_identity.current",
    "aws_s3_bucket.state",
    "aws_s3_bucket_versioning.state",
  ].join("\n");

  assert.deepEqual(missingResources(stateList), BUCKET_RESOURCES.slice(2));
});

test("nothing is missing once all six are in", () => {
  assert.deepEqual(missingResources(BUCKET_RESOURCES.join("\n") + "\n"), []);
});