# 《火种不灭：致未知的征服者》

Muse 原创 45 秒竖屏短片工程。

## 一键重建

在 Windows PowerShell 中执行：

~~~powershell
cd "<MUSE_ROOT>\ember-fire"
powershell -ExecutionPolicy Bypass -File .\build.ps1
~~~

默认会重新生成香港粤语女声旁白（Microsoft Edge TTS 的 zh-HK-HiuMaanNeural）、原创配乐与拟音、逐帧动画、最终 MP4、QA 报告和工程 ZIP。需要网络才能在线合成粤语语音；已缓存的音频可用 -SkipTTS 复用。

## 交付结构

- 火种不灭.mp4 — 1080×1920、24 fps、45 秒
- 火种不灭.srt — 繁体粤语字幕
- poster.jpg — 海报\n- contact-sheet.jpg — 全片风格帧联系表
- TREATMENT.md / STYLE.md — 导演方案与原创视觉规则
- voice/ — 旁白原文、各句 WAV、语音时长
- out/mix.wav — 原创配乐、环境声、拟音和旁白混音
- QA_REPORT.md — 媒体探测、全片解码、响度、黑帧和字幕检查
- ember-fire-project.zip — 可重建工程与交付素材

## 声音

粤语旁白使用香港女声 zh-HK-HiuMaanNeural，速度 -8%、音高 -4 Hz。配乐与拟音由本工程程序生成，不含商业音乐采样。旁白语音服务及依赖见 CREDITS。

## 说明

当前版本的画面为原创程序动画，并非 Wan 生成的实拍感 AI 镜头。ComfyUI/Wan 实机路径尚未通过本轮验证，不会将程序动画标注成 Wan 生成。
