import test from "node:test";
import assert from "node:assert/strict";
import { filterCommands, isCommandPaletteShortcut } from "./command-palette.js";

const commands = [
  { id: "overview", label: "意识中枢", keywords: ["总览", "状态"], target: "#overview" },
  { id: "inspiration", label: "灵感工坊", keywords: ["想法", "研究"], target: "#inspirations" },
  { id: "video", label: "视频创作", keywords: ["wan", "mp4", "剪辑"], target: "#video-studio" },
];

test("command search matches labels and keywords without case sensitivity", () => {
  assert.deepEqual(filterCommands(commands, "WAN").map((item) => item.id), ["video"]);
  assert.deepEqual(filterCommands(commands, "想法").map((item) => item.id), ["inspiration"]);
});

test("command search returns a stable limited list and all commands for empty query", () => {
  assert.equal(filterCommands(commands, "").length, 3);
  assert.deepEqual(filterCommands(commands, "不存在"), []);
  assert.equal(filterCommands(commands, "", 2).length, 2);
});

test("command palette opens on Ctrl/Cmd+K or slash outside editable fields only", () => {
  assert.equal(isCommandPaletteShortcut({ key: "k", ctrlKey: true, metaKey: false, targetTag: "DIV" }), true);
  assert.equal(isCommandPaletteShortcut({ key: "k", ctrlKey: false, metaKey: true, targetTag: "DIV" }), true);
  assert.equal(isCommandPaletteShortcut({ key: "/", ctrlKey: false, metaKey: false, targetTag: "DIV" }), true);
  assert.equal(isCommandPaletteShortcut({ key: "/", ctrlKey: false, metaKey: false, targetTag: "TEXTAREA" }), false);
  assert.equal(isCommandPaletteShortcut({ key: "k", ctrlKey: true, metaKey: false, targetTag: "INPUT" }), false);
});
