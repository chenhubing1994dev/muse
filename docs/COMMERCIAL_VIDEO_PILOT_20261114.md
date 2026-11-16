# Muse 商業視頻試產報告（2026-11-14）

## 結果摘要

**狀態：第二版已產出。** 本次完成一條 24.5 秒、9:16 的廣東話字幕短片，主題為「AI 講得咁肯定，就一定啱？｜三步查證法」。
- 最終影片：`data/prometheus/high-value-short-cantonese-pilot-v2-motion/ai-verification-three-steps-9x16.mp4`
- 格式：1080×1920、24 FPS、H.264/AAC stereo、24.5 秒。
- 編碼：AMD AMF H.264；檔案 20,819,365 bytes。
- MP4 SHA-256：`bb730818b58099c5ccccd8696da06d91bc6606b4eeb2356c47baac26a42570de`
- 完整解碼：通過。
- 交付 ZIP：`data/prometheus/high-value-short-cantonese-pilot-v2-motion/delivery-bundle.zip`
- ZIP：11 個項目、43,943,665 bytes、SHA-256 `69a5f5387fbaec22ece9bef385af124bf57b9752dbf71ea4f58b446eddae03a2`，CRC 通過。
- Creator Initiative：`a9b2d46a5b8649958264f07eec7b6210`
- Product/version：`a382ac96a1dc3fe598ff0b20e3d9d62c / a83887741d032079c578`
- Evolution Cycle：`cycle_65d150253a244f2c9d1855d2582bf1f7`
- Research Wiki：本輪 source、claim、idea 三個節點均已連接到影片 artifact／Creator Initiative。

## 製作方法與真實邊界

- 畫面重用六段此前已實機生成的 Wan 2.1 T2V 1.3B 鏡頭；原始素材為 288×512、12 FPS。
- 本輪沒有重新啟動 ComfyUI，也沒有新跑 Wan 模型推理；使用 FFmpeg 做畫幅統一、Ken Burns 輕微推進、字幕浮動、淡入淡出、音軌及 AMF 編碼。
- 聲音是原創合成氛圍音，不是旁白。檢查未找到可用的本機中文／廣東話 TTS 模型或系統語音，因此沒有假稱完成粵語配音。
- 廣東話字幕及 CTA：「留言『清單』索取免費 AI 查證清單」；交付包附 `lead-magnet-checklist.md` 與 `commercial-test-plan.md`。
- 研究依據：NIST AI 600-1（2024）§2.2；三步查證法是編輯建議，不是 NIST 認證方法。來源見 `source-notes.json`。

## QA 與版本選擇

- 第一版 `high-value-short-cantonese-pilot-v1`：MP4 解碼及基本規格通過，但 QA 有 8 項靜止畫面／持續暗場警告，故不選作最終試產版本。
- 第二版 `high-value-short-cantonese-pilot-v2-motion`：QA status=passed；1080×1920、24 FPS、24.5 秒、H.264/AAC、完整解碼通過；black intervals、frozen intervals、silent intervals 均為空；warnings=0。
- 產品狀態仍是 `testing`，`ready_to_publish=false`。技術 QA 通過不等於完成商業審片、權利／披露檢查或平台發布授權。

## 商業驗證方案

- 目標受眾：日常使用生成式 AI 的創作者、自由工作者及小型商戶。
- 痛點：錯誤資訊或虛構引用造成返工與信任損失。
- 免費引流品：三步 AI 查證清單。
- CTA：留言「清單」。
- 後續可測付費品：AI 研究／查證工作流模板或小型導入服務；尚未有訂單或收入證據。
- 發布後觀測：3 秒留存、平均觀看時長、完播率、CTA 留言、清單點擊／下載、合格線索、訂單、實際結算收入與製作成本。
- 本輪未發布、未連接社交帳號、未產生收入；不得把播放量推算成收入。

## 尚待補齊

1. 本機沒有可用粵語 TTS；下一步應選擇可商用授權的本地粵語聲線，實際合成並做聽感／響度 QA。
2. 部分舊 Wan 鏡頭的原始 prompt／seed 資料不完整；storyboard 已記錄各源 MP4 SHA-256，但完整素材來源與模型權利仍須逐項核對。
3. VideoProductBuilder 的產品 manifest 目前未把六段源鏡頭列入 `source_assets`；需要把 shot-level asset hashes／prompt provenance 接入產品 manifest。
4. 發布前需人工審片、檢查字幕／內容／音量／素材商用權利，並由使用者完成平台帳號授權。
5. 真實發布後再收集平台數據；只有訂單及官方結算／實際到帳才可計入收入。

## 相關檔案

- 最終 MP4：`data/prometheus/high-value-short-cantonese-pilot-v2-motion/ai-verification-three-steps-9x16.mp4`
- 最終交付 ZIP：`data/prometheus/high-value-short-cantonese-pilot-v2-motion/delivery-bundle.zip`
- QA：`data/products/a382ac96a1dc3fe598ff0b20e3d9d62c/a83887741d032079c578/qa-report.json`
- 生成記錄：`data/prometheus/high-value-short-cantonese-pilot-v2-motion/generation.json`
- 本報告：`docs/COMMERCIAL_VIDEO_PILOT_20261114.md`
