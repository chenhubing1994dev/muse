<script lang="ts">
  import type { VideoRecipeTrace } from "./api";
  export let review: VideoRecipeTrace["release_review"];
  export let contactSheetUrl = "";
  export let contactSheetMessage = "";

  const statusLabel = (value: string | undefined): string => ({
    blocked_pending_human_review: "等待人工审核",
    ready_for_separate_release_authorization: "等待独立发布授权",
    verified_human_review_digest_scope_bound: "人工审核记录已核验",
    heuristics_only_review_required: "仍需人工复核",
    pending_human_review: "待人工复核",
    not_recorded: "未记录",
    evidence_mismatch_or_incomplete: "证据不完整或不匹配",
    not_connected: "未连接",
    review_required: "需要审核",
    confirmation_required: "需要确认",
  }[value ?? ""] ?? value ?? "未知");

  const checkLabel = (value: string): string => ({
    technical_quality: "技术质量",
    visual_anomaly_review: "视觉异常复核",
    subtitle_review: "字幕审校",
    editorial_and_fact_review: "事实与编辑审核",
    visual_and_model_rights_review: "视觉与模型权利",
    privacy_and_person_release_review: "隐私与肖像授权",
    music_and_audio_rights_review: "音乐与音频权利",
    ai_generated_content_disclosure: "AI 内容披露",
    sponsorship_affiliate_and_product_placement: "商业关系披露",
    platform_upload_specifications: "平台规格",
    account_authorization: "账号授权",
  }[value] ?? value);
</script>

<style>
  .contact-sheet-review { display: grid; gap: 0.55rem; margin: 0.9rem 0; padding: 0.8rem; border: 1px solid var(--line, rgba(160, 180, 210, 0.2)); border-radius: 0.7rem; }
  .contact-sheet-review img { display: block; width: 100%; max-height: 28rem; object-fit: contain; background: #090d18; border-radius: 0.45rem; }
  .contact-sheet-review small { color: var(--muted, #aab4c7); }
</style>

<section class="release-review-detail" aria-label="发布质量报告核验摘要">
  <div class="video-recipe-trace-grid">
    <span>
      <b>报告完整性</b>
      <small>{review.quality_report_matches_package === true ? "报告哈希与发布包清单一致" : review.quality_report_matches_package === false ? "报告缺失或哈希/内容不匹配" : "旧记录未提供核验结果"}</small>
    </span>
    <span>
      <b>质量结论</b>
      <small>{statusLabel(review.overall_status ?? review.status)}</small>
    </span>
    {#if review.quality_report_sha256}
      <span>
        <b>报告 SHA-256</b>
        <small>{review.quality_report_sha256.slice(0, 20)}</small>
      </span>
    {/if}
  </div>
  {#if review.quality_report_matches_package === true && review.blocking_items?.length}
    <div class="release-review-blockers">
      <b>待处理阻断项（{review.blocking_items.length}）</b>
      <ul>
        {#each review.blocking_items as item (item)}
          <li>{checkLabel(item)}</li>
        {/each}
      </ul>
    </div>
  {/if}
  {#if review.quality_report_matches_package === true && review.checks && Object.keys(review.checks).length}
    <div class="video-recipe-trace-grid" aria-label="逐项审核状态">
      {#each Object.entries(review.checks) as entry (entry[0])}
        <span>
          <b>{checkLabel(entry[0])}</b>
          <small>{statusLabel(entry[1].status)}</small>
        </span>
      {/each}
    </div>
  {/if}
  {#if review.checks?.visual_anomaly_review?.contact_sheet}
    <div class="contact-sheet-review" aria-label="关键帧联系表人工审片">
      <b>关键帧联系表 · 人工视觉复核</b>
      {#if contactSheetUrl}
        <img src={contactSheetUrl} alt="已核验的视频关键帧联系表" loading="lazy" />
        <small>联系表哈希已由本地 API 对发布包清单和质量报告复核。请人工检查主体裁切、构图、镜头衔接与异常帧。</small>
      {:else}
        <small>{contactSheetMessage || "正在读取联系表…"}</small>
      {/if}
    </div>
  {/if}
  <small>仅展示已与发布包哈希核验的质量报告；报告完整不等于发布授权，发布始终保持关闭。</small>
</section>
