<script lang="ts">
  import type { ApiClient, InitiativePerformanceSnapshot, InitiativePerformanceAnalysis } from "./api";
  export let client: ApiClient;
  export let initiativeId: string;

  let expanded = false;
  let loading = false;
  let saving = false;
  let message = "";
  let snapshots: InitiativePerformanceSnapshot[] = [];

  let analysis: InitiativePerformanceAnalysis | null = null;
  let sourceReference = "";
  let platform: "youtube" | "tiktok" | "douyin" | "instagram" | "facebook" | "bilibili" | "other" = "youtube";
  let currency = "USD";
  let views = "0";
  let likes = "0";
  let comments = "0";
  let saves = "0";
  let shares = "0";
  let ctaClicks = "0";
  let qualifiedLeads = "0";
  let orders = "0";
  let reportedRevenue = "0";
  let settledRevenue = "0";
  let productionCost = "0";
  let retention = "";
  let completion = "";
  let averageWatch = "";
  let notes = "";
  let topic = "";
  let hookStyle = "";
  let narrativeStructure = "";
  let editingPace = "";
  let visualStyle = "";
  let voiceLanguage = "粤语（香港）";
  let ctaStyle = "";
  let contentFormat = "短视频";

  async function load(): Promise<void> {
    loading = true;
    message = "";
    try {
      const result = await client.initiativePerformance(initiativeId);
      snapshots = result.snapshots;

      analysis = result.analysis;
    } catch (error) {
      message = error instanceof Error ? error.message : "商业表现读取失败";
    } finally {
      loading = false;
    }
  }

  function count(value: string, label: string): number {
    const parsed = Number(value);
    if (!Number.isSafeInteger(parsed) || parsed < 0) throw new Error(label + "必须是非负整数");
    return parsed;
  }

  function optionalRate(value: string, label: string): number | undefined {
    if (!value.trim()) return undefined;
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < 0 || parsed > 100) throw new Error(label + "须介于 0 至 100");
    return parsed;
  }

  async function save(): Promise<void> {
    if (saving) return;
    saving = true;
    message = "";
    try {
      const metrics: Record<string, number> = {
        views: count(views, "播放量"),
        likes: count(likes, "点赞"),
        comments: count(comments, "评论"),
        saves: count(saves, "收藏"),
        shares: count(shares, "分享"),
        cta_clicks: count(ctaClicks, "CTA 点击"),
        qualified_leads: count(qualifiedLeads, "合格线索"),
        orders: count(orders, "订单"),
        reported_revenue_minor: count(reportedRevenue, "报告收入"),
        settled_revenue_minor: count(settledRevenue, "已结算收入"),
        production_cost_minor: count(productionCost, "制作成本"),
      };
      const retentionValue = optionalRate(retention, "3 秒留存率");
      const completionValue = optionalRate(completion, "完播率");
      if (retentionValue !== undefined) metrics.three_second_retention_pct = retentionValue;
      if (completionValue !== undefined) metrics.completion_rate_pct = completionValue;
      if (averageWatch.trim()) {
        const parsed = Number(averageWatch);
        if (!Number.isFinite(parsed) || parsed < 0) throw new Error("平均观看秒数必须是非负数字");
        metrics.avg_watch_seconds = parsed;
      }
      const contentProfile: Record<string, string> = {};
      for (const [key, value] of Object.entries({
        topic, hook_style: hookStyle, narrative_structure: narrativeStructure,
        editing_pace: editingPace, visual_style: visualStyle,
        voice_language: voiceLanguage, cta_style: ctaStyle, format: contentFormat
      })) {
        if (value.trim()) contentProfile[key] = value.trim();
      }
      await client.recordInitiativePerformance(initiativeId, {
        idempotency_key: crypto.randomUUID().replaceAll("-", ""),
        platform,
        source: "manual_entry",
        currency: currency.trim().toUpperCase(),
        source_reference: sourceReference.trim(),
        metrics,
        notes: notes.trim(),
        content_profile: contentProfile,
      });
      message = "表现快照已追加保存；历史记录不会被覆盖。"
      sourceReference = "";
      notes = "";
      await load();
    } catch (error) {
      message = error instanceof Error ? error.message : "商业表现保存失败";
    } finally {
      saving = false;
    }
  }
</script>

<section class="initiative-performance">
  <button type="button" class="radar-scan" aria-expanded={expanded} onclick={async () => { expanded = !expanded; if (expanded && !snapshots.length) await load(); }}>
    {expanded ? "收起商业表现" : "商业表现 / 收益回流"}
  </button>
  {#if expanded}
    <div class="performance-panel">
      <div class="worker-heading"><b>作品商业反馈账本</b><button type="button" class="radar-scan" onclick={load} disabled={loading}>{loading ? "读取中…" : "刷新数据"}</button></div>
      {#if analysis?.cohorts.length}
        <div class="performance-summary">
          {#each analysis.cohorts as cohort (`${cohort.platform}-${cohort.source}-${cohort.currency}`)}
            <div>
              <b>{cohort.platform} · {cohort.source === "official_analytics" ? "官方数据" : "人工录入"} · {cohort.currency}</b>
              <small>{cohort.status === "ready_for_review" ? "达到复核门槛" : "样本不足"} · {cohort.sample_count}/{cohort.min_sample_count} 条作品 · {cohort.total_views.toLocaleString()}/{cohort.min_views.toLocaleString()} 播放</small>
              <small>互动率 {cohort.rates.engagement_per_view_pct == null ? "—" : cohort.rates.engagement_per_view_pct.toFixed(2) + "%"} · CTA {cohort.rates.cta_clicks_per_view_pct == null ? "—" : cohort.rates.cta_clicks_per_view_pct.toFixed(2) + "%"} · 完播 {cohort.rates.completion_rate_pct == null ? "—" : cohort.rates.completion_rate_pct.toFixed(1) + "%"}</small>
              <small>报告收入 {cohort.revenue_minor.reported.toLocaleString()} · 已结算 {cohort.revenue_minor.settled.toLocaleString()} · 制作成本 {cohort.revenue_minor.production_cost.toLocaleString()}</small>
              {#if cohort.item_comparisons.three_second_retention_pct.status === "descriptive_comparison"}
                <small>留存较高：{cohort.item_comparisons.three_second_retention_pct.top.map((item) => item.source_reference + " (" + item.rates.three_second_retention_pct + "%)").join("、")}</small>
                <small>留存较低：{cohort.item_comparisons.three_second_retention_pct.bottom.map((item) => item.source_reference + " (" + item.rates.three_second_retention_pct + "%)").join("、")}</small>
              {:else}
                <small>单作品留存比较样本不足（每条作品至少 {cohort.item_comparison_min_views} 播放）</small>
              {/if}
              {#if cohort.warnings.length}<small>{cohort.warnings.join(" · ")}</small>{/if}
            </div>
          {/each}
        </div>
        <small>分析只使用每条作品最新快照；不同平台、来源与币种分开。作品排序仅针对单项指标，不代表综合成功排名或因果结论。</small>
      {:else if snapshots.length}
        <p class="empty-state compact">已录入快照，但作品标识不足，暂不能进行作品间比较。</p>
      {:else}
        <p class="empty-state compact">暂无已记录的商业表现快照。</p>
      {/if}
      <form class="performance-form" onsubmit={(event) => { event.preventDefault(); void save(); }}>
        <label>平台
          <select bind:value={platform}>
            <option value="youtube">YouTube</option>
            <option value="tiktok">TikTok</option>
            <option value="douyin">抖音</option>
            <option value="instagram">Instagram</option>
            <option value="facebook">Facebook</option>
            <option value="bilibili">哔哩哔哩</option>
            <option value="other">其他</option>
          </select>
        </label>
        <label>币种
          <input bind:value={currency} maxlength="3" pattern="[A-Za-z]{3}" required />
        </label>
        <label class="performance-notes">作品标识（作品链接或平台 ID）<input bind:value={sourceReference} maxlength="500" required placeholder="用于识别同一作品的后续快照" /></label>
        <label>选题主题<input bind:value={topic} maxlength="80" placeholder="如：AI 创作、商业洞察" /></label>
        <label>开场钩子<input bind:value={hookStyle} maxlength="80" placeholder="如：反常识提问、结果先行" /></label>
        <label>叙事结构<input bind:value={narrativeStructure} maxlength="80" placeholder="如：问题-冲突-解决" /></label>
        <label>剪辑节奏<input bind:value={editingPace} maxlength="80" placeholder="如：快切、舒缓递进" /></label>
        <label>视觉风格<input bind:value={visualStyle} maxlength="80" placeholder="如：电影写实、赛博朋克" /></label>
        <label>旁白语言/声线<input bind:value={voiceLanguage} maxlength="80" placeholder="如：粤语女声" /></label>
        <label>CTA 设计<input bind:value={ctaStyle} maxlength="80" placeholder="如：评论引导、订阅引导" /></label>
        <label>作品形式<input bind:value={contentFormat} maxlength="80" placeholder="如：短视频、教程、故事片" /></label>
        <label>播放量<input type="number" min="0" step="1" bind:value={views} /></label>
        <label>点赞<input type="number" min="0" step="1" bind:value={likes} /></label>
        <label>评论<input type="number" min="0" step="1" bind:value={comments} /></label>
        <label>收藏<input type="number" min="0" step="1" bind:value={saves} /></label>
        <label>分享<input type="number" min="0" step="1" bind:value={shares} /></label>
        <label>CTA 点击<input type="number" min="0" step="1" bind:value={ctaClicks} /></label>
        <label>合格线索<input type="number" min="0" step="1" bind:value={qualifiedLeads} /></label>
        <label>订单<input type="number" min="0" step="1" bind:value={orders} /></label>
        <label>报告收入（最小单位）<input type="number" min="0" step="1" bind:value={reportedRevenue} /></label>
        <label>已结算收入（最小单位）<input type="number" min="0" step="1" bind:value={settledRevenue} /></label>
        <label>制作成本（最小单位）<input type="number" min="0" step="1" bind:value={productionCost} /></label>
        <label>3 秒留存率 %<input type="number" min="0" max="100" step="0.01" bind:value={retention} /></label>
        <label>完播率 %<input type="number" min="0" max="100" step="0.01" bind:value={completion} /></label>
        <label>平均观看秒数<input type="number" min="0" step="0.01" bind:value={averageWatch} /></label>
        <label class="performance-notes">备注<textarea bind:value={notes} maxlength="500" rows="2" placeholder="可注明数据来源或结算周期"></textarea></label>
        <button type="submit" class="feedback-submit" disabled={saving || loading}>{saving ? "保存中…" : "追加表现快照"}</button>
      </form>
      {#if message}<p class="feedback-message" role="status">{message}</p>{/if}
      {#if snapshots.length}
        <div class="worker-heading"><b>最近快照</b><span>{snapshots.length} 条</span></div>
        <div class="feedback-list">
          {#each snapshots.slice(0, 8) as item (item.id)}
            <div class="feedback-row">
              <b>{item.platform} · {item.currency}</b>
              <span>播放 {Number(item.views).toLocaleString()} · 订单 {Number(item.orders).toLocaleString()} · 已结算 {Number(item.settled_revenue_minor).toLocaleString()}</span>
              <small>{item.source === "manual_entry" ? "人工录入" : "官方分析"} · {item.captured_at}</small>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  {/if}
</section>
<style>
  .initiative-performance { margin: 0.65rem 0 0.9rem; }
  .performance-panel { margin-top: 0.7rem; padding: 0.9rem; border: 1px solid rgba(105, 184, 255, 0.24); border-radius: 12px; background: rgba(7, 16, 34, 0.55); display: grid; gap: 0.75rem; }
  .performance-summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 0.55rem; }
  .performance-summary > div { display: grid; gap: 0.25rem; padding: 0.6rem; border: 1px solid rgba(105, 184, 255, 0.18); border-radius: 8px; }
  .performance-form { display: grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); gap: 0.6rem; }
  .performance-form label { display: grid; gap: 0.25rem; font-size: 0.78rem; color: var(--muted, #9eb1ce); }
  .performance-form input, .performance-form select, .performance-form textarea { width: 100%; min-width: 0; }
  .performance-form .performance-notes { grid-column: 1 / -1; }
  .performance-form button { justify-self: start; }
</style>

