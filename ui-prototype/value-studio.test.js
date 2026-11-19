import test from 'node:test';
import assert from 'node:assert/strict';
import { scoreIdea, toInspirationBrief, VALUE_DIMENSIONS } from './value-studio.js';

test('scoring dimensions keep documented weights and bounded 0-5 scores', () => {
  const totalWeight = VALUE_DIMENSIONS.reduce((sum, item) => sum + item.weight, 0);
  assert.ok(Math.abs(totalWeight - 1) < 1e-9);
  const result = scoreIdea('如何用一个简单短片对比三种入门方法，附实测数据和工作流拆解');
  assert.equal(result.scores.length, 6);
  for (const item of result.scores) {
    assert.ok(item.score >= 0 && item.score <= 5, `${item.key} score must be bounded`);
  }
  assert.ok(result.total >= 0 && result.total <= 100);
  assert.ok(['S', 'A', 'B', 'C'].includes(result.tier));
  assert.equal(result.nature, 'heuristic_estimate');
});

test('specific high-value brief scores higher than vague text', () => {
  const strong = scoreIdea('新手如何用 3 个步骤做出高留存短视频？实测数据对比，独家方法拆解，适合抖音与B站，附工作流演示和长期复用模板');
  const vague = scoreIdea('随便做个东西');
  assert.ok(strong.total > vague.total, `${strong.total} should exceed ${vague.total}`);
  assert.ok(strong.structure.includes('指定目标受众'));
  assert.ok(strong.structure.includes('包含具体数字'));
});

test('risky claims are flagged and penalized but never auto-rejected', () => {
  const clean = scoreIdea('如何做一根视频教程，带实测数据与镜头演示');
  const risky = scoreIdea('如何稳赚不赔做视频，保证赚钱，搬运免费资源就行');
  assert.ok(risky.riskFlags.some((flag) => flag.code === 'income_guarantee'));
  assert.ok(risky.riskFlags.some((flag) => flag.code === 'rights_risk'));
  assert.ok(risky.total <= clean.total);
  assert.ok(risky.total >= 0);
  assert.equal(risky.empty, false);
});

test('empty input returns explicit empty state instead of fabricated score', () => {
  const result = scoreIdea('   ');
  assert.equal(result.empty, true);
  assert.equal(result.wordCount, 0);
});

test('inspiration brief preserves original text and epistemic labels', () => {
  const result = scoreIdea('用数据拆解新手避坑方法，附实测对比');
  const brief = toInspirationBrief(result, '原始灵感文本');
  assert.ok(brief.includes('原始灵感文本'));
  assert.ok(brief.includes('启发式评估'));
  assert.ok(brief.includes(result.label));
});
