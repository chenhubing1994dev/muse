from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import zipfile
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_RUN = ROOT / "data/prometheus/monetization-ai-factcheck-canto-20261123"
VERIFIED_RUN = ROOT / "data/prometheus/runs/cantonese_pilot_v2/15d2ac0f52e44796b5bf7b9fd8b8b822"
OLD_PRODUCT = ROOT / "data/products/d81700135fe112b661e0176b5f34e12a/1713600cfe751184fc96"
OUTPUT = ROOT / "data/prometheus/monetization-ai-factcheck-canto-20261123-verified"
TITLE = "AI 講得咁肯定，就一定真？三步查證保住專業信任"
NARRATION = (
    "你有冇試過，見到人工智能講得好肯定，就以為一定係真？"
    "記住，講得流暢唔代表有證據。"
    "第一，拆開人名、日期同數字，逐樣核實。"
    "第二，返去睇原始來源，唔好只睇轉述。"
    "第三，主動搵反面證據。"
    "搵唔到可靠來源，就標示未證實。"
)

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def write_text(relative: str, value: str) -> Path:
    target = OUTPUT / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"refusing to overwrite campaign file: {relative}")
    target.write_text(value.rstrip() + "\n", encoding="utf-8")
    return target

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def ensure_regular(path: Path) -> Path:
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise RuntimeError(f"refusing symlink path: {path}")
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.resolve(strict=True)

def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing output: {OUTPUT}")
    for path in (
        SOURCE_RUN / "ai-verification-three-steps-9x16.mp4",
        SOURCE_RUN / "ambient-original.wav",
        SOURCE_RUN / "subtitles.srt",
        SOURCE_RUN / "cover.jpg",
        VERIFIED_RUN / "generation.json",
        OLD_PRODUCT / "manifest.json",
        OLD_PRODUCT / "product.zip",
    ):
        ensure_regular(path)

    old_generation = load_json(VERIFIED_RUN / "generation.json")
    old_manifest = load_json(OLD_PRODUCT / "manifest.json")
    asr = old_generation.get("narration_asr")
    if not isinstance(asr, dict) or asr.get("status") != "passed":
        raise RuntimeError("historical Cantonese ASR evidence is not a passed record")
    if abs(float(asr.get("comparison", {}).get("cer", 1.0)) - 0.091954) > 1e-6:
        raise RuntimeError("historical Cantonese ASR CER does not match the verified acceptance record")
    if asr.get("audio_sha256") != old_manifest.get("audio_asset", {}).get("sha256"):
        raise RuntimeError("historical ASR record is not bound to the source product narration WAV")
    if asr.get("subtitles", {}).get("sha256") != old_manifest.get("files", {}).get("subtitles.srt"):
        raise RuntimeError("historical ASR subtitles are not bound to the product SRT")
    if asr.get("human_listening_review_required") is not True:
        raise RuntimeError("historical record must retain human listening review requirement")
    if old_generation.get("narration_asr", {}).get("comparison", {}).get("status") != "passed":
        raise RuntimeError("historical ASR comparison is not passed")

    source_mp4 = SOURCE_RUN / "ai-verification-three-steps-9x16.mp4"
    ambient = SOURCE_RUN / "ambient-original.wav"
    source_subtitles = SOURCE_RUN / "subtitles.srt"
    source_cover = SOURCE_RUN / "cover.jpg"
    old_zip = OLD_PRODUCT / "product.zip"

    OUTPUT.mkdir(parents=True, exist_ok=False)
    try:
        with tempfile.TemporaryDirectory(prefix=".muse-verified-voice-", dir=OUTPUT) as temp_name:
            temp = Path(temp_name)
            with zipfile.ZipFile(old_zip, "r") as archive:
                names = set(archive.namelist())
                required = {"narration/voiceover.wav", "subtitles.srt"}
                if not required.issubset(names):
                    raise RuntimeError(f"historical product ZIP is missing required members: {sorted(required - names)}")
                archive.extract("narration/voiceover.wav", temp)
                archive.extract("subtitles.srt", temp)
            voice = ensure_regular(temp / "narration/voiceover.wav")
            asr_srt = ensure_regular(temp / "subtitles.srt")
            expected_voice_hash = old_manifest["audio_asset"]["sha256"]
            expected_srt_hash = asr["subtitles"]["sha256"]
            if sha256(voice) != expected_voice_hash or sha256(voice) != asr["audio_sha256"]:
                raise RuntimeError("extracted narration WAV hash does not match the previously accepted ASR evidence")
            if sha256(asr_srt) != expected_srt_hash:
                raise RuntimeError("extracted Whisper SRT hash does not match the previously accepted ASR evidence")

            recipe_path = ROOT / "scripts" / "muse-high-value-short.py"
            spec = importlib.util.spec_from_file_location("muse_high_value_short_finalize", recipe_path)
            if spec is None or spec.loader is None:
                raise RuntimeError("could not load the current Muse media helper")
            recipe = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(recipe)
            inventory = recipe.discover_media_tools()
            tools = {item.tool_id: item.executable for item in inventory.tools}
            ffprobe, ffmpeg = tools.get("ffprobe"), tools.get("ffmpeg")
            if not ffprobe or not ffmpeg:
                raise RuntimeError("FFprobe and FFmpeg are required for final media verification")

            probe = json.loads(recipe.run([
                ffprobe, "-v", "error", "-show_entries",
                "format=duration,size:stream=codec_name,codec_type,width,height,avg_frame_rate,sample_rate,channels",
                "-of", "json", str(source_mp4)
            ], ROOT, timeout=40).stdout)
            video = next((item for item in probe.get("streams", []) if item.get("codec_type") == "video"), None)
            audio = next((item for item in probe.get("streams", []) if item.get("codec_type") == "audio"), None)
            if not video or video.get("codec_name") != "h264" or video.get("width") != 1080 or video.get("height") != 1920:
                raise RuntimeError(f"source campaign render failed video stream checks: {probe}")
            fps = float(Fraction(video.get("avg_frame_rate", "0/1")))
            duration = float(probe.get("format", {}).get("duration", 0))
            if abs(fps - 24.0) > 0.001 or abs(duration - 24.5) > 0.3:
                raise RuntimeError(f"source campaign render has unexpected frame rate or duration: {probe}")
            recipe.run([ffmpeg, "-hide_banner", "-v", "error", "-i", str(source_mp4), "-f", "null", "-"], ROOT, timeout=600)

            from muse.video_product_builder import VideoProductBuilder
            builder = VideoProductBuilder(ROOT)
            generation_metadata = {
                "status": "completed",
                "model": "Wan 2.1 T2V 1.3B (six existing AI shots) + FFmpeg editorial assembly",
                "aspect_ratio": "9:16",
                "fps": int(round(fps)),
                "duration_seconds": duration,
                "frames": int(round(duration * fps)),
                "width": int(video["width"]),
                "height": int(video["height"]),
                "bytes": source_mp4.stat().st_size,
                "has_audio": bool(audio),
            }
            scene_records = [
                {"order": 1, "prompt": "Existing verified Wan 2.1 source shot 01; original prompt text not retained.", "generation_status": "generated", "source_file": "shot-01.mp4", "duration_seconds": 2.083333, "width": 288, "height": 512, "fps": 12},
                {"order": 2, "prompt": "Existing verified Wan 2.1 source shot 02; original prompt text not retained.", "generation_status": "generated", "source_file": "shot-02.mp4", "duration_seconds": 2.083333, "width": 288, "height": 512, "fps": 12},
                {"order": 3, "prompt": "Existing verified Wan 2.1 source shot 03; original prompt text not retained.", "generation_status": "generated", "source_file": "shot-03.mp4", "duration_seconds": 2.083333, "width": 288, "height": 512, "fps": 12},
                {"order": 4, "prompt": "A single warm golden ember awakens in an immense deep indigo cosmic void, delicate nebula clouds and distant stars slowly emerge, cinematic macro-to-wide camera movement, awe and mystery, premium science fiction film cinematography, volumetric light, no text, no letters, no logos.", "prompt_id": "cf75de4e-c4ee-4b32-a39a-12cf9ddc8ab6", "seed": 20261114, "generation_status": "generated", "source_file": "shot-04.mp4", "duration_seconds": 2.083333, "width": 288, "height": 512, "fps": 12},
                {"order": 5, "prompt": "Existing verified Wan 2.1 source shot 05; source prompt text not retained in the source folder.", "prompt_id": "5863beb5-122d-45c1-b32e-1dcd8dab2999", "seed": 20261112, "generation_status": "generated", "source_file": "shot-05.mp4", "duration_seconds": 5.083333, "width": 288, "height": 512, "fps": 12},
                {"order": 6, "prompt": "Existing verified Wan 2.1 source shot 06; source prompt text not retained in the source folder.", "prompt_id": "6bd3aff6-ad05-46cb-bc2b-ef92f19a81f0", "seed": 20261113, "generation_status": "generated", "source_file": "shot-06.mp4", "duration_seconds": 5.083333, "width": 288, "height": 512, "fps": 12},
            ]
            result = builder.package_generated_clip(
                TITLE,
                "Educational vertical short: split AI claims into checkable parts, inspect the original source, and actively search for counter-evidence.",
                source_mp4,
                generation_metadata=generation_metadata,
                scene_records=scene_records,
                narration_path=voice,
                narration_script=NARRATION,
                subtitle_path=asr_srt,
                narration_asr=asr,
                background_music_path=ambient,
            )
            verified = builder.verify_path(
                ROOT / "data" / "products",
                result["initiative_id"],
                result["version_id"],
            )
            if not verified.get("validated") or not verified.get("mp4_sha256"):
                raise RuntimeError(f"VideoProductBuilder did not validate the final product: {verified}")
            product_dir = ROOT / result["relative_path"]
            final_video = ensure_regular(product_dir / "video.mp4")
            final_zip = ensure_regular(product_dir / "product.zip")
            final_srt = ensure_regular(product_dir / "subtitles.srt")
            shutil.copy2(final_video, OUTPUT / "ai-verification-three-steps-9x16.mp4")
            shutil.copy2(final_zip, OUTPUT / "video-product.zip")
            shutil.copy2(final_srt, OUTPUT / "narration-subtitles-whisper.srt")
            shutil.copy2(source_subtitles, OUTPUT / "on-screen-captions.srt")
            shutil.copy2(source_cover, OUTPUT / "cover.jpg")
            shutil.copy2(product_dir / "manifest.json", OUTPUT / "video-product-manifest.json")
            shutil.copy2(product_dir / "qa-report.json", OUTPUT / "video-qa-report.json")
            shutil.copy2(product_dir / "storyboard.json", OUTPUT / "storyboard.json")

        free_checklist = """# AI 答案三步查證清單（免費版）

適用：用 AI 做研究、寫稿、簡報、客戶提案或社群內容的人。

## 1. 拆開主張
把答案拆成可逐項核對的句子，圈出人名、日期、數字、引文、因果關係及具體承諾。不要把整段流暢文字當成一個已證實的結論。

## 2. 查原始來源
優先找原始文件、官方資料、研究原文或當事方紀錄。核對作者、日期、上下文及原文是否真的支持這個主張。二手文章只可作線索，不可自動當作原始證據。

## 3. 主動找反證
搜尋相反研究、不同解釋、失敗案例和資料更新。記錄支持與反對證據；來源不足時，清楚標示「未證實」，不要補寫不存在的引文。

## 一條主張的記錄欄
主張｜來源 URL｜原文摘錄｜支持證據｜反面證據｜來源獨立性｜目前判斷｜信心｜待解問題

**使用提醒：** 這是一份實務工作清單，不是法律、醫療、投資或合規意見，也不是 NIST 對本清單的認證。
"""

        publish_copy = """# 發布文案｜AI 查證三步法

## TikTok / Reels / Shorts 標題
AI 講得咁肯定，就一定真？記住呢 3 步查證法

## 影片描述
你用 AI 做研究、寫稿或整簡報時，最怕唔係答案唔夠流暢，而係錯誤資料包裝得似真。

記住三步：
1. 拆開人名、日期、數字和引文；
2. 追返原始來源，核對上下文；
3. 主動搵反證，有疑問就標示未證實。

留言「清單」，索取免費 AI 查證表。想試用完整研究工作流模板，可留言「工作流」登記測試版。

研究依據：NIST AI 600-1（2024-07-26）§2.2 及 MS-2.5-003。三步清單和主動找反證屬本片的實務編輯建議，不是 NIST 原文清單或官方認證。

## 置頂留言
免費版查證表可以先用；如果你想要主張登記、來源評分、反證紀錄和影片發布檢查表的完整模板，留言「工作流」。我會先收集需求，再開放小批測試。

## Hashtags
#人工智能 #AI查證 #內容創作 #自由工作者 #知識管理 #研究方法

## 披露提示
發布時按平台當期規則標示 AI 生成／合成畫面及合成旁白。若加入聯盟連結、贊助或付費推薦，必須另行清楚披露；不可暗示自己使用過未實際試用的產品。
"""

        monetization_plan = """# 商業試產計劃｜AI 研究與查證工作流模板包

## 目標客群
經常用生成式 AI 做研究、寫內容、製作提案或社群貼文的創作者、自由工作者、顧問與小型商戶。先以粵語／繁體中文受眾測試，不預設市場需求已成立。

## 問題與價值主張
問題：AI 可能自信地輸出錯誤內容或誤導性引用；使用者需要一個簡單、可重複的核對流程。
價值：把「拆主張—查原始來源—找反證—留下判斷紀錄」變成可複用的工作模板，減少遺漏並方便團隊交接。
邊界：模板不保證零錯誤、不替代專業審核，也不是 NIST 認證工具。

## 漏斗設計
1. 短片：展示一個真實痛點和三步方法。
2. 免費引流：觀眾留言「清單」，取得一頁式免費查證表。
3. 付費候選：完整模板包，含主張登記 CSV、來源評分、反證日誌、提示詞、發布前檢查表和每週內容實驗表。
4. 先做小批付費測試；有真實付款及使用回饋後才擴大，不先投入廣告預算。

## 價格假設（不是市場行情）
可對相近受眾測試兩個一次性假設價，例如 HK$39 與 HK$79；產品內容、流量來源和測試期間保持一致。沒有結帳頁或付款紀錄之前，不宣稱已有付費需求。

## 測試指標
3 秒留存、平均觀看時間、完播率、留言「清單」數、免費清單交付數、合格工作流需求、結帳開始、實際付款、退款、平台費、淨收入和每條影片製作成本。
播放量、點讚或留言不是收入；只把已核實付款／結算列作收入。

## 首輪實驗
先在 2 個平台發布同一母版，使用相同 CTA；記錄發布時間和每個平台的原始報表。首輪以 7–14 日作觀察窗口，樣本不足只記錄方向，不聲稱統計顯著。依照真實留言問題決定下一條影片，而非憑空猜測爆款。

## 上架前硬門檻
人工完整審片、聽審粵語聲線、校對字幕、確認來源與素材權利、按平台規則披露 AI 生成內容、建立可履約的交付方式、確認付款及退款流程。以上未完成前，產品維持測試狀態。
"""

        paid_readme = """# AI 研究與查證工作流模板包（測試版）

這是一套可編輯的 Markdown / CSV 工作模板，適合創作者、自由工作者、顧問及小型商戶建立可追溯的 AI 研究流程。

## 包含
- claim-register.csv：主張與判斷登記；
- source-review-rubric.md：來源品質評分；
- counterevidence-log.md：反面證據與替代解釋；
- prompt-pack.md：拆主張、找原始來源、找反證及整理簡報的提示詞；
- editorial-release-checklist.md：影片／文章發布前檢查；
- weekly-content-sprint.csv：內容實驗追蹤。

## 使用方式
複製模板，逐條記錄主張和來源；支持與反對材料分開保存；未確認的部分維持「未證實」。每次發布保留版本和人工審核紀錄。

此包是工作模板，不是法律／醫療／財務意見，不保證消除 AI 錯誤，也不代表任何標準機構認證。
"""

        source_rubric = """# 來源品質評分卡

每項 0–2 分；總分只作排序提示，不取代編輯判斷。

| 維度 | 0 分 | 1 分 | 2 分 |
|---|---|---|---|
| 來源層級 | 無法追溯 | 二手轉述 | 原始文件／官方資料 |
| 作者與責任 | 不明 | 部分可識別 | 作者／機構及責任清楚 |
| 日期與時效 | 缺失／過期 | 日期可知但時效存疑 | 日期明確且適合該主張 |
| 方法與上下文 | 無方法／斷章取義 | 有部分方法 | 方法、範圍及限制清楚 |
| 獨立佐證 | 只有單一來源 | 多處轉載同一來源 | 多個真正獨立來源 |
| 反面材料 | 沒有查找 | 找到但未評估 | 已記錄反證及替代解釋 |

總分 0–12。低分或互相矛盾的主張應標示「未證實／待覆核」，不得只因分數高就自動視為真實。
"""

        counter_log = """# 反證與替代解釋日誌

- 主張 ID：
- 原始主張：
- 最強支持證據：
- 最強反面證據：
- 反面來源是否獨立：
- 有沒有其他合理解釋：
- 哪一項新證據會推翻目前判斷：
- 尚未找到的資料：
- 暫定狀態：未核實 / 有支持但有限 / 存在爭議 / 已被反證
- 信心及理由：
- 審核人與日期：
"""

        prompt_pack = """# AI 研究與查證提示詞

## A. 拆解主張
請把以下文本拆成原子化、可查證的主張。分別標記事實、推論、意見、假設和虛構；圈出人名、日期、數字、引文、因果說法。不得替缺失來源補造引用。

## B. 追查原始來源
對每條主張列出最可能的原始來源類型、可直接開啟的來源 URL、作者／機構、日期、支持該主張的原文位置，以及來源沒有證明的部分。找不到時寫「未找到」，不要猜測。

## C. 主動找反證
請先嘗試推翻主張：尋找反例、相反研究、方法限制、更新資料和替代解釋。分清真正獨立的來源與互相轉載。不要只列支持材料。

## D. 編輯摘要
輸出：已核實內容、未核實內容、爭議、來源品質、反證、信心及限制。每個重要句子都要能追溯到來源；避免虛構統計、專家背書和親身體驗。
"""

        release_checklist = """# 編輯與發布前檢查表

- [ ] 每個重要事實主張都有可開啟來源和對應原文位置
- [ ] 已記錄支持證據、反面證據、替代解釋和不確定性
- [ ] 沒有把推論、意見或靈感包裝成已證實事實
- [ ] 沒有捏造統計、專家身份、用戶評價或個人試用經歷
- [ ] 圖像、影片、字體、音樂、聲音及肖像的商用權利已核實
- [ ] AI 生成畫面／合成旁白按平台現行規則披露
- [ ] 聯盟、贊助、付費推廣關係如存在，已清楚披露
- [ ] 字幕、旁白、封面和片中事實經人工覆核
- [ ] 平台格式和帳號資格按發布當日官方資料核對
- [ ] 已確認交付、付款、退款和客戶支援方式
"""

        write_text("README.md", f"""# {TITLE}

狀態：testing，尚未核准發布。

## 影片
- 24.5 秒，1080×1920，24fps，9:16，H.264/AAC，48 kHz 立體聲。
- 六段既有 Wan 2.1 T2V 鏡頭；本輪使用 FFmpeg 重新剪輯和繁體字幕。
- 粵語旁白使用此前同一文稿且已通過 Muse ASR 門檻的本地音檔；音檔 SHA-256 與舊產品記錄一致，CER 0.091954（門檻 0.10）。本輪新合成聲音曾觸發 CER 0.344828，已拒絕使用。
- 目前 Canto-TTS 只有單一中性預設音色，不是成熟女聲；仍需人工聽審和字幕校對。
- Whisper SRT 保留識別原文，可能存在錯字；畫面內繁體字幕與 SRT 分開提供。

## 商業用途
用短片引導觀眾索取免費查證清單，再收集對完整研究工作流模板包的真實需求。HK$39 / HK$79 只是待測價格假設，不是既有市場報價。

## 發布限制
- 產品技術驗證不等於已通過人工審片、版權、平台或帳戶審核。
- QA 警告（若存在）需人工檢查；平台發布、付款及收入追蹤未接通。
- 沒有任何實際收入數據；不保證賺錢。
""")
        write_text("FREE_CHECKLIST.md", free_checklist)
        write_text("PUBLISH_COPY.md", publish_copy)
        write_text("MONETIZATION_PLAN.md", monetization_plan)
        write_text("paid-workflow-kit/README.md", paid_readme)
        write_text("paid-workflow-kit/source-review-rubric.md", source_rubric)
        write_text("paid-workflow-kit/counterevidence-log.md", counter_log)
        write_text("paid-workflow-kit/prompt-pack.md", prompt_pack)
        write_text("paid-workflow-kit/editorial-release-checklist.md", release_checklist)

        claim_csv = OUTPUT / "paid-workflow-kit" / "claim-register.csv"
        with claim_csv.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow([
                "claim_id", "claim_text", "claim_type", "source_url", "source_author",
                "source_date", "supporting_excerpt", "counter_evidence", "source_independence",
                "status", "confidence", "reviewer", "reviewed_at", "open_questions"
            ])
            writer.writerow(["C-001", "", "fact/inference/opinion/hypothesis", "", "", "",
                             "", "", "", "unverified", "", "", "", ""])
        weekly_csv = OUTPUT / "paid-workflow-kit" / "weekly-content-sprint.csv"
        weekly_csv.write_text(
            "week,topic,hypothesis,hook_variant,platform,post_date,views,avg_watch_seconds,completion_rate,comments,checklist_deliveries,qualified_leads,paid_orders,refunds,net_revenue,cost,learning\n"
            "2026-W48,AI claim verification,Creators need a repeatable evidence log,AI sounds confident but is it true?,,,,,,,,,,,,,\n",
            encoding="utf-8",
        )
        write_text("SOURCE-NOTES.md", """# 研究來源與內容邊界

- NIST AI 600-1，*Artificial Intelligence Risk Management Framework: Generative Artificial Intelligence Profile*，2024-07-26，§2.2、MS-2.5-003。
- DOI：<https://doi.org/10.6028/NIST.AI.600-1>
- PDF：<https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf>
- NIST 的內容用作 AI confabulation 與核對生成來源／引文的依據；本片「拆主張、查原始來源、找反證」是編輯整理的實務方法，不是 NIST 官方認證或逐字清單。
- 原始 Wan 鏡頭中的部分提示詞未保留；對應鏡頭在分鏡中明確標示來源提示詞缺失，不補造原始提示詞。
""")

        product_manifest = load_json(OUTPUT / "video-product-manifest.json")
        product_qa = load_json(OUTPUT / "video-qa-report.json")
        video_path = OUTPUT / "ai-verification-three-steps-9x16.mp4"
        cover_path = OUTPUT / "cover.jpg"
        product_zip = OUTPUT / "video-product.zip"
        asr_report = {
            "status": asr["status"],
            "cer": asr["comparison"]["cer"],
            "max_cer": asr["comparison"]["max_cer"],
            "audio_sha256": asr["audio_sha256"],
            "subtitle_sha256": asr["subtitles"]["sha256"],
            "model_revision": asr["model_revision"],
            "human_listening_review_required": True,
        }
        manifest = {
            "schema_version": 1,
            "title": TITLE,
            "status": "testing_not_ready_to_publish",
            "audience": "Cantonese-speaking AI creators, freelancers, consultants and small businesses",
            "offer": "Free AI verification checklist leading to a paid workflow-template beta",
            "video": {
                "path": video_path.name,
                "sha256": sha256(video_path),
                "bytes": video_path.stat().st_size,
                "duration_seconds": product_manifest["video_metadata"]["duration_seconds"],
                "width": product_manifest["video_metadata"]["width"],
                "height": product_manifest["video_metadata"]["height"],
                "fps": product_manifest["video_metadata"]["fps"],
                "codec": "h264",
                "audio_codec": "aac",
                "full_decode": "passed_by_VideoProductBuilder",
            },
            "voiceover": {
                "language": "zh-HK",
                "engine": "local Canto-TTS ONNX, previously accepted exact audio asset",
                "audio_sha256": asr_report["audio_sha256"],
                "asr": asr_report,
                "voice_profile": "single neutral default voice; not a mature female timbre",
                "human_listening_review_required": True,
                "reused_from_prior_verified_product": True,
            },
            "product_quality_gate": product_manifest["quality_gate"],
            "paid_kit_files": [
                "paid-workflow-kit/claim-register.csv",
                "paid-workflow-kit/source-review-rubric.md",
                "paid-workflow-kit/counterevidence-log.md",
                "paid-workflow-kit/prompt-pack.md",
                "paid-workflow-kit/editorial-release-checklist.md",
                "paid-workflow-kit/weekly-content-sprint.csv",
            ],
            "publish_api_called": False,
            "ready_to_publish": False,
            "revenue": {"status": "not_observed", "actual_revenue": None},
            "disclosures": [
                "AI-generated Wan footage and synthetic Cantonese narration require platform review/disclosure.",
                "Current Cantonese TTS uses one neutral default voice; human listening approval remains mandatory.",
                "The short is a lead-generation asset, not evidence of platform revenue eligibility or earnings.",
            ],
        }
        write_text("CAMPAIGN-MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        asset_paths = [
            path for path in sorted(OUTPUT.rglob("*"))
            if path.is_file() and path.name not in {"monetization-campaign.zip", "DELIVERY-VERIFY.json"}
        ]
        asset_hashes = [
            {"path": path.relative_to(OUTPUT).as_posix(), "bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in asset_paths
        ]
        with zipfile.ZipFile(OUTPUT / "monetization-campaign.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in asset_paths:
                archive.write(path, path.relative_to(OUTPUT).as_posix())
        with zipfile.ZipFile(OUTPUT / "monetization-campaign.zip") as archive:
            bad_member = archive.testzip()
            members = archive.namelist()
            if bad_member is not None:
                raise RuntimeError(f"campaign ZIP CRC failed: {bad_member}")
            if len(members) < 15:
                raise RuntimeError(f"campaign ZIP member count unexpectedly low: {len(members)}")

        verification = {
            "status": "validated",
            "output_dir": OUTPUT.relative_to(ROOT).as_posix(),
            "video_sha256": sha256(video_path),
            "cover_sha256": sha256(cover_path),
            "video_product_zip_sha256": sha256(product_zip),
            "campaign_zip": "monetization-campaign.zip",
            "campaign_zip_bytes": (OUTPUT / "monetization-campaign.zip").stat().st_size,
            "campaign_zip_sha256": sha256(OUTPUT / "monetization-campaign.zip"),
            "zip_members": len(members),
            "zip_crc": "passed",
            "assets": asset_hashes,
            "product_quality_gate": product_manifest["quality_gate"],
            "actual_revenue": None,
            "publish_api_called": False,
            "ready_to_publish": False,
        }
        write_text("DELIVERY-VERIFY.json", json.dumps(verification, ensure_ascii=False, indent=2))
        print(json.dumps(verification, ensure_ascii=False, indent=2))
        return 0
    except Exception:
        # Keep the output directory as a diagnostic record; never delete or overwrite it automatically.
        raise

if __name__ == "__main__":
    raise SystemExit(main())
