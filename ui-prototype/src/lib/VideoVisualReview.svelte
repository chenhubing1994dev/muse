<script lang="ts">
  import { createEventDispatcher } from "svelte";
  import type { VisualReviewPayload, VisualReviewRecord, VisualReviewFinding, VisualRevisionBrief } from "./api";

  export let imageUrl = "";
  export let message = "";
  export let busy = false;
  export let reviews: VisualReviewRecord[] = [];
  export let revisionBrief: VisualRevisionBrief | null = null;

  const dispatch = createEventDispatcher<{ submit: VisualReviewPayload }>();
  let reviewer = "";
  let reviewerRole = "video_editor";
  let rationale = "";
  let decision: VisualReviewPayload["decision"] = "changes_requested";
  let confirmation = "";
  let checklist = {
    subject_framing: false,
    shot_continuity: false,
    visual_anomalies: false,
    subtitle_visibility: false,
    full_video_playback: false,
  };
  let findings: VisualReviewFinding[] = [{
    timecode_ms: 0, shot_id: "", severity: "major", category: "continuity",
    description: "", expected_fix: "",
  }];
  $: checklistComplete = Object.values(checklist).every(Boolean);
  $: findingsComplete = decision !== "changes_requested" || (findings.length > 0 && findings.every((item) =>
    Number.isInteger(item.timecode_ms) && item.timecode_ms >= 0 && item.timecode_ms <= 86400000
    && item.description.trim().length >= 10 && item.expected_fix.trim().length >= 10));
  function addFinding() {
    if (findings.length >= 20) return;
    findings = [...findings, { timecode_ms: 0, shot_id: "", severity: "major", category: "continuity", description: "", expected_fix: "" }];
  }
  function removeFinding(index: number) {
    if (findings.length <= 1) return;
    findings = findings.filter((_, itemIndex) => itemIndex !== index);
  }
  function formatTimecode(milliseconds: number): string {
    const totalSeconds = Math.floor(milliseconds / 1000);
    return String(Math.floor(totalSeconds / 3600)).padStart(2, "0") + ":"
      + String(Math.floor(totalSeconds % 3600 / 60)).padStart(2, "0") + ":"
      + String(totalSeconds % 60).padStart(2, "0");
  }

  function submitReview(event: SubmitEvent) {
    event.preventDefault();
    if (busy || !checklistComplete || !findingsComplete || confirmation !== "RECORD-VISUAL-REVIEW") return;
    dispatch("submit", {
      decision, reviewer, reviewer_role: reviewerRole, rationale,
      checklist: { ...checklist },
      findings: decision === "changes_requested" ? findings.map((item) => ({ ...item, shot_id: item.shot_id.trim(), description: item.description.trim(), expected_fix: item.expected_fix.trim() })) : [],
      confirmation: "RECORD-VISUAL-REVIEW",
    });
    // A second append requires a fresh, deliberate checklist and confirmation.
    confirmation = "";
    checklist = {
      subject_framing: false,
      shot_continuity: false,
      visual_anomalies: false,
      subtitle_visibility: false,
      full_video_playback: false,
    };
  }
</script>

<style>
  .visual-review-workbench {
    display: grid;
    gap: 0.75rem;
    margin: 0.9rem 0;
    padding: 1rem;
    border: 1px solid var(--line, rgba(160, 180, 210, 0.2));
    border-radius: 0.8rem;
    background: rgba(8, 14, 28, 0.62);
  }
  .visual-review-workbench img {
    display: block;
    width: 100%;
    max-height: 38rem;
    object-fit: contain;
    background: #050811;
    border-radius: 0.55rem;
  }
  .visual-review-workbench ul {
    display: grid;
    gap: 0.55rem;
    margin: 0.25rem 0;
    padding-left: 1.2rem;
  }
  .visual-review-workbench small { color: var(--muted, #aab4c7); }
  .visual-review-workbench .visual-review-warning {
    color: #e8c98b;
    border-left: 2px solid rgba(232, 201, 139, 0.6);
    padding-left: 0.7rem;
  }
  .visual-review-workbench form { display: grid; gap: 0.7rem; }
  .visual-review-workbench label { display: grid; gap: 0.3rem; }
  .visual-review-workbench input:not([type="checkbox"]), .visual-review-workbench select,
  .visual-review-workbench textarea {
    width: 100%; box-sizing: border-box; padding: 0.55rem 0.65rem;
    color: inherit; background: rgba(4, 9, 20, 0.8);
    border: 1px solid var(--line, rgba(160, 180, 210, 0.25)); border-radius: 0.45rem;
  }
  .visual-review-workbench .review-check { display: flex; align-items: flex-start; gap: 0.5rem; }
  .visual-review-workbench button {
    justify-self: start; padding: 0.55rem 0.9rem; border-radius: 0.45rem;
    border: 1px solid var(--line, rgba(160, 180, 210, 0.3)); color: inherit;
    background: rgba(44, 80, 125, 0.35); cursor: pointer;
  }
  .visual-review-workbench button:disabled { opacity: 0.45; cursor: not-allowed; }
  .review-history { border-top: 1px solid var(--line, rgba(160, 180, 210, 0.2)); padding-top: 0.75rem; }
  .review-history article { padding: 0.6rem 0; border-bottom: 1px solid var(--line, rgba(160, 180, 210, 0.12)); }
  .revision-findings, .revision-brief { display: grid; gap: .65rem; border-top: 1px solid var(--line, rgba(160,180,210,.2)); padding-top: .75rem; }
  .revision-findings fieldset, .revision-brief article { display: grid; gap: .5rem; border: 1px solid var(--line, rgba(160,180,210,.2)); border-radius: .5rem; padding: .7rem; }
  .revision-findings legend { color: var(--muted, #aab4c7); }
  .revision-findings small, .revision-brief small { color: var(--muted, #aab4c7); overflow-wrap: anywhere; }
</style>

<section class="visual-review-workbench" aria-label="视频人工视觉审片工作台">
  <div>
    <b>人工视觉审片 · 关键帧联系表</b>
    <p>先检查关键帧，再打开完整原片核对镜头之间的运动与连续性。</p>
  </div>
  {#if imageUrl}
    <img src={imageUrl} alt="已通过本地产品证据校验的视频关键帧联系表" loading="lazy" />
    <small>联系表与原片 SHA-256 已由本地服务端核验，审核记录会绑定当前产品版本和两份素材哈希。</small>
  {:else}
    <small role="status">{message || "正在读取已核验的联系表…"}</small>
  {/if}
  <div>
    <b>审核清单</b>
    <ul>
      <li><label class="review-check"><input type="checkbox" bind:checked={checklist.subject_framing} /> 人物、主体和关键物体没有意外裁切</label></li>
      <li><label class="review-check"><input type="checkbox" bind:checked={checklist.shot_continuity} /> 镜头构图、景别、方向和动作衔接合理</label></li>
      <li><label class="review-check"><input type="checkbox" bind:checked={checklist.visual_anomalies} /> 已检查重复帧、黑帧、异常空镜和画面瑕疵</label></li>
      <li><label class="review-check"><input type="checkbox" bind:checked={checklist.subtitle_visibility} /> 字幕、标题没有遮挡主体或关键画面</label></li>
      <li><label class="review-check"><input type="checkbox" bind:checked={checklist.full_video_playback} /> 已打开并完整回看原片，不只看联系表</label></li>
    </ul>
  </div>
  <small class="visual-review-warning">审核人姓名和角色是本机用户自报信息，不是账号实名认证。SHA-256 可发现素材变化，不是数字签名。版权、肖像、音乐、事实和发布授权仍须分别审核。</small>
  <form on:submit={submitReview}>
    <label>审核人（本机自报）
      <input bind:value={reviewer} minlength="2" maxlength="80" required placeholder="填写审核人显示名称" />
    </label>
    <label>审核角色
      <input bind:value={reviewerRole} minlength="2" maxlength="60" required />
    </label>
    <label>审核结论
      <select bind:value={decision}>
        <option value="approved">画面审核通过</option>
        <option value="changes_requested">需要修改</option>
        <option value="rejected">不予通过</option>
      </select>
    </label>
    <label>审核理由（至少 20 字）
      <textarea bind:value={rationale} minlength="20" maxlength="2000" rows="3" required placeholder="记录发现的问题、关键时间点或通过理由…"></textarea>
    </label>
    {#if decision === "changes_requested"}
      <div class="revision-findings">
        <b>返工问题清单（每项必须绑定时间点）</b>
        {#each findings as finding, index}
          <fieldset>
            <legend>问题 {index + 1}</legend>
            <label>时间点（毫秒）
              <input type="number" min="0" max="86400000" step="1000" bind:value={finding.timecode_ms} required />
              <small>{formatTimecode(Number(finding.timecode_ms) || 0)}</small>
            </label>
            <label>镜头编号（可选）<input bind:value={finding.shot_id} maxlength="80" placeholder="例如 shot-03" /></label>
            <label>严重程度
              <select bind:value={finding.severity}><option value="blocker">阻断</option><option value="major">严重</option><option value="minor">一般</option></select>
            </label>
            <label>问题类别
              <select bind:value={finding.category}>
                <option value="framing">主体构图</option><option value="continuity">镜头连贯</option>
                <option value="visual_artifact">画面瑕疵</option><option value="subtitle">字幕</option>
                <option value="audio">音频</option><option value="other">其他</option>
              </select>
            </label>
            <label>问题描述（至少 10 字）<textarea bind:value={finding.description} minlength="10" maxlength="300" rows="2" required></textarea></label>
            <label>预期修复（至少 10 字）<textarea bind:value={finding.expected_fix} minlength="10" maxlength="300" rows="2" required></textarea></label>
            <button type="button" disabled={findings.length <= 1} on:click={() => removeFinding(index)}>移除此问题</button>
          </fieldset>
        {/each}
        <button type="button" disabled={findings.length >= 20} on:click={addFinding}>增加返工问题（最多 20 项）</button>
      </div>
    {/if}
    <label>明确确认（输入 RECORD-VISUAL-REVIEW）
      <input bind:value={confirmation} required autocomplete="off" placeholder="RECORD-VISUAL-REVIEW" />
    </label>
    <button type="submit" disabled={busy || !imageUrl || !checklistComplete || !findingsComplete || confirmation !== "RECORD-VISUAL-REVIEW" || reviewer.trim().length < 2 || rationale.trim().length < 20}>
      {busy ? "正在写入不可覆盖审核账本…" : "提交并追加视觉审核记录"}
    </button>
  </form>
  {#if message}<small role="status">{message}</small>{/if}
  {#if revisionBrief}
    <div class="revision-brief" aria-label="当前版本返工清单">
      <b>返工清单 · {revisionBrief.tasks.length} 项</b>
      <small>来源审核记录 {revisionBrief.source_review_id} · 原版本 {revisionBrief.source_version} · 视频 SHA-256 {revisionBrief.source_video_sha256.slice(0, 16)}… · {revisionBrief.evidence_status}</small>
      {#each revisionBrief.tasks as task}
        <article>
          <b>{task.severity === "blocker" ? "阻断" : task.severity === "major" ? "严重" : "一般"} · {task.category} · {formatTimecode(task.timecode_ms)}</b>
          {#if task.shot_id}<small> · {task.shot_id}</small>{/if}
          <p>{task.description}</p>
          <small>预期修复：{task.expected_fix}</small>
          <small>任务 ID：{task.task_id} · 待处理</small>
        </article>
      {/each}
      <small class="visual-review-warning">这是由最新审核摘要确定性生成的只读返工清单；不会自动改写视频，也不会创建新版本。</small>
    </div>
  {/if}
  <div class="review-history">
    <b>审核历史 · {reviews.length} 条</b>
    {#if reviews.length === 0}
      <small>当前版本尚无正式视觉审核记录。</small>
    {:else}
      {#each reviews as review (review.record_id)}
        <article>
          <b>{review.decision === "approved" ? "审核通过" : review.decision === "changes_requested" ? "需要修改" : "不予通过"}</b>
          <small> · {review.reviewer.label}（{review.reviewer.role}） · {new Date(review.reviewed_at).toLocaleString()}</small>
          <p>{review.rationale}</p>          {#if review.findings?.length}
            <ul>
              {#each review.findings as finding}
                <li><b>{formatTimecode(finding.timecode_ms)} · {finding.severity} · {finding.category}</b>：{finding.description}；预期修复：{finding.expected_fix}</li>
              {/each}
            </ul>
          {/if}
          <small>证据校验：{review.evidence_status || "已写入，待重新核验"} · SHA-256 {review.review_sha256.slice(0, 16)}…</small>
        </article>
      {/each}
    {/if}
  </div>
</section>
