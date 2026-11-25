(() => {
  const form = document.getElementById("inquiry-form");
  const output = document.getElementById("inquiry-output");
  if (!form || !output) return;

  function submissionId() {
    if (form.dataset.submissionId) return form.dataset.submissionId;
    if (!window.crypto || typeof window.crypto.getRandomValues !== "function") {
      throw new Error("当前浏览器无法安全生成提交编号。");
    }
    const bytes = window.crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
    form.dataset.submissionId = [
      hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16),
      hex.slice(16, 20), hex.slice(20)
    ].join("-");
    return form.dataset.submissionId;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const button = form.querySelector('button[type="submit"]');
    const originalLabel = button ? button.textContent : "";
    if (button) {
      button.disabled = true;
      button.textContent = "正在提交到本机…";
    }
    output.hidden = false;
    output.textContent = "正在连接本机收件服务；服务器确认前不会显示提交成功。";
    try {
      const values = new FormData(form);
      const payload = {
        service: values.get("service"),
        brief: values.get("brief"),
        budget: values.get("budget"),
        timeline: values.get("timeline"),
        contact: values.get("contact"),
        consent: form.querySelector('input[name="consent"]').checked,
        website: values.get("website"),
        submission_id: submissionId()
      };
      const response = await fetch("/api/leads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        credentials: "same-origin",
        cache: "no-store"
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || result.status !== "accepted" || !result.lead_reference) {
        if (response.status === 429) throw new Error("提交过于频繁，请稍后再试。");
        if (response.status === 403) throw new Error("来源校验未通过；请从本机预览页面重新打开表单。");
        if (response.status === 503) throw new Error("本机收件数据库暂不可用，资料未确认保存。");
        throw new Error("本机服务未接受这份询价；资料未确认保存。");
      }
      output.textContent = [
        "本机已收到询价。",
        "询价编号：" + result.lead_reference,
        "本地保存期限：" + result.retention_days + " 日。",
        "资料保存在本机 SQLite，不会自动发送给第三方。"
      ].join("\n");
      form.reset();
      delete form.dataset.submissionId;
    } catch (error) {
      output.textContent = error instanceof Error
        ? error.message + "\n请确认本机 Muse Lead Server 正在运行；失败的提交可安全重试。"
        : "提交失败，资料未确认保存。请检查本机服务后重试。";
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = originalLabel;
      }
    }
  });
})();