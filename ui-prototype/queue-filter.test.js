import test from "node:test";
import assert from "node:assert/strict";
import { filterQueueTasks } from "./queue-filter.js";

const tasks = [
  { id: "task-a17", title: "生成宣传片", status: "running" },
  { id: "task-b22", title: "整理研究资料", status: "queued" },
  { id: "task-c03", title: "审查视频成品", status: "waiting_approval" },
  { id: "task-d04", title: "修复渲染失败", status: "failed" },
];

test("queue search matches task title and id without case sensitivity", () => {
  assert.deepEqual(filterQueueTasks(tasks, "宣传片").map((task) => task.id), ["task-a17"]);
  assert.deepEqual(filterQueueTasks(tasks, "TASK-B22").map((task) => task.id), ["task-b22"]);
});

test("queue status filter selects exact status and composes with search", () => {
  assert.deepEqual(filterQueueTasks(tasks, "", "queued").map((task) => task.id), ["task-b22"]);
  assert.deepEqual(filterQueueTasks(tasks, "视频", "waiting_approval").map((task) => task.id), ["task-c03"]);
  assert.deepEqual(filterQueueTasks(tasks, "视频", "failed"), []);
});

test("empty search preserves source order and does not mutate the source array", () => {
  const before = tasks.map((task) => task.id);
  assert.deepEqual(filterQueueTasks(tasks, "  ").map((task) => task.id), before);
  assert.deepEqual(tasks.map((task) => task.id), before);
});

test("unknown status and unmatched query safely return an empty result", () => {
  assert.deepEqual(filterQueueTasks(tasks, "", "unknown"), []);
  assert.deepEqual(filterQueueTasks(tasks, "不存在"), []);
});
