(() => {
  const form = document.getElementById("inquiry-form");
  const output = document.getElementById("inquiry-output");
  if (!form || !output) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const values = new FormData(form);
    const message = [
      "MUSE 项目合作询价",
      "项目类型：" + (values.get("service") || ""),
      "需求描述：" + (values.get("brief") || ""),
      "预算范围：" + (values.get("budget") || "暂未确定"),
      "期望时间：" + (values.get("timeline") || "弹性安排"),
      "联系信息：" + (values.get("contact") || "未提供"),
      "",
      "备注：以上内容由用户主动生成；发送前请自行核对。"
    ].join("\n");
    output.hidden = false;
    output.textContent = message;
    try {
      await navigator.clipboard.writeText(message);
      output.textContent += "\n\n已复制到剪贴板。页面不会自动发送或上传这些内容。";
    } catch (_) {
      output.textContent += "\n\n浏览器未授予剪贴板权限，请手动复制上方内容。";
    }
  });
})();