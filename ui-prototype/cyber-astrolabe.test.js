import test from "node:test";
import assert from "node:assert/strict";
import {
  buildBaguaRingItems,
  buildPrometheusStageItems,
  buildStarMansionRingItems,
  describeArcPath,
  layoutAgentGalaxy,
  layoutKnowledgeConstellation,
  polarToCartesian,
} from "./cyber-astrolabe.js";

test("polarToCartesian places 0 deg at 12 o'clock and 90 deg at 3 o'clock", () => {
  assert.deepEqual(polarToCartesian(100, 100, 50, 0), { x: 100, y: 50 });
  assert.deepEqual(polarToCartesian(100, 100, 50, 90), { x: 150, y: 100 });
  assert.ok(describeArcPath(100, 100, 50, 0, 120).startsWith("M "));
});

test("ring builders produce 8 Bagua trigrams, 28 Star Mansions, and 5 Prometheus stages", () => {
  const trigrams = Array.from({ length: 8 }, (_, i) => ({
    name: `卦${i}`,
    bits_bottom_to_top: i.toString(2).padStart(3, "0"),
  }));
  const bagua = buildBaguaRingItems(trigrams, 260, 260, 136);
  assert.equal(bagua.length, 8);
  assert.equal(bagua[0].angleDeg, 0);
  assert.deepEqual(bagua[7].linesTopDown, [true, true, true]);

  const mansions = Array.from({ length: 28 }, (_, i) => ({ name: `宿${i}`, quadrant: "azure_dragon" }));
  const ring28 = buildStarMansionRingItems(mansions, 260, 260, 212);
  assert.equal(ring28.length, 28);
  assert.ok(ring28.every((item) => Number.isFinite(item.x) && Number.isFinite(item.lx)));

  const stages = buildPrometheusStageItems({
    ignition: { inspirations_count: 3 },
    discovery: { knowledge_nodes_total: 911 },
    synthesis: { knowledge_edges_total: 2459 },
    creation: { products_count: 4 },
    transmission: { progress_events_count: 3000 },
  });
  assert.equal(stages.length, 5);
  assert.equal(stages[1].metric, 911);
});

test("layoutKnowledgeConstellation positions all nodes deterministically within bounds", () => {
  const nodes = [
    { id: "n1", node_type: "claim", degree: 10 },
    { id: "n2", node_type: "source", degree: 4 },
    { id: "n3", node_type: "idea", degree: 2 },
  ];
  const edges = [{ id: "e1", source_id: "n1", target_id: "n2", edge_type: "supports" }];
  const layout = layoutKnowledgeConstellation(nodes, edges, 800, 500);
  assert.equal(layout.nodes.length, 3);
  assert.equal(layout.edges.length, 1);
  for (const node of layout.nodes) {
    assert.ok(node.x >= 34 && node.x <= 766);
    assert.ok(node.y >= 34 && node.y <= 466);
    assert.ok(node.r >= 3.6);
  }
});

test("layoutAgentGalaxy arranges commander center and orbital rings", () => {
  const galaxy = layoutAgentGalaxy({
    commander: { title: "Muse Commander" },
    specialist_profiles: [{ specialist_id: "researcher" }, { specialist_id: "architect" }],
    live_agents: [],
    capabilities: [{ capability_id: "c1" }],
  }, 600, 440);
  assert.equal(galaxy.center.x, 300);
  assert.equal(galaxy.center.y, 220);
  assert.equal(galaxy.specialists.length, 2);
  assert.equal(galaxy.liveAgents.length, 0);
  assert.equal(galaxy.capabilities.length, 1);
});
