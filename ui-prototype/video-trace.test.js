import test from "node:test";
import assert from "node:assert/strict";
import { resolveProductAttempt } from "./src/lib/video-trace.js";

const product = {
  id: "initiative-1234567890abcdef-version-12345678901234567890",
  initiative_id: "initiative-1234567890abcdef",
  version_id: "12345678901234567890",
};
const attempt = {
  attempt_id: "attempt-123",
  product_id: product.initiative_id,
  version_id: product.version_id,
};

test("product attempt lookup uses initiative_id and exact version, not composite product id", () => {
  assert.deepEqual(resolveProductAttempt(product, [attempt]), {
    status: "matched", attemptId: "attempt-123",
  });
});

test("product attempt lookup refuses a different product version", () => {
  assert.deepEqual(resolveProductAttempt(product, [{ ...attempt, version_id: "different-version" }]), {
    status: "missing", attemptId: null,
  });
});

test("product attempt lookup reports missing without guessing", () => {
  assert.deepEqual(resolveProductAttempt(product, []), { status: "missing", attemptId: null });
});

test("product attempt lookup refuses ambiguous duplicate matches", () => {
  assert.deepEqual(resolveProductAttempt(product, [attempt, { ...attempt, attempt_id: "attempt-456" }]), {
    status: "ambiguous", attemptId: null,
  });
});
