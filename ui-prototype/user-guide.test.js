import test from "node:test";
import assert from "node:assert/strict";
import { USER_GUIDE_STEPS, getGuideStep, getGuideProgress } from "./user-guide.js";

test("user guide covers each existing primary workspace with a real anchor", () => {
  const ids = USER_GUIDE_STEPS.map((step) => step.id);
  assert.deepEqual(ids, [
    "overview",
    "celestial-lab",
    "knowledge-starmap",
    "agents",
    "prometheus-core",
    "inspiration",
    "video",
    "queue",
    "evolution",
  ]);
  for (const step of USER_GUIDE_STEPS) {
    assert.ok(step.title && step.purpose && step.howToUse && step.result && step.target.startsWith("#"));
  }
});

test("guide step selection clamps to the available range", () => {
  assert.equal(getGuideStep(-2).id, USER_GUIDE_STEPS[0].id);
  assert.equal(getGuideStep(999).id, USER_GUIDE_STEPS.at(-1).id);
});

test("guide progress reports a one-based position and bounded percentage", () => {
  assert.deepEqual(getGuideProgress(0), { current: 1, total: 9, percent: 11 });
  assert.deepEqual(getGuideProgress(8), { current: 9, total: 9, percent: 100 });
});
