from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "prometheus" / "monetization-ai-factcheck-canto-20261123"
TITLE = "AI 講得咁肯定，就一定真？三步查證保住專業信任"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

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
        raise FileExistsError(f"refusing to overwrite existing campaign file: {relative}")
    target.write_text(value.rstrip() + "\n", encoding="utf-8")
    return target

def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing output: {OUTPUT}")

    source_script = ROOT / "scripts" / "muse-high-value-short.py"
    spec = importlib.util.spec_from_file_location("muse_high_value_short", source_script)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load the existing Muse video recipe")
    recipe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recipe)

    recipe.SCENES = [
        {"id": "shot-01", "file": "shot-01.mp4", "duration": 2.083333,
         "caption": "AI 講得咁肯定\n就一定係真？", "prompt_id": None, "seed": None},
        {"id": "shot-02", "file": "shot-02.mp4", "duration": 2.083333,
         "caption": "流暢表達\n唔等於有證據。", "prompt_id": None, "seed": None},
        {"id": "shot-03", "file": "shot-03.mp4", "duration": 2.083333,
         "caption": "① 拆開主張\n人名、日期、數字逐項核實。", "prompt_id": None, "seed": None},
        {"id": "shot-04", "file": "shot-04.mp4", "duration": 2.083333,
         "caption": "② 查原始來源\n原文真係支持個講法？",
         "prompt_id": "cf75de4e-c4ee-4b32-a39a-12cf9ddc8ab6", "seed": 20261114},
        {"id": "shot-05", "file": "shot-05.mp4", "duration": 5.083333,
         "caption": "③ 主動搵反證\n睇吓有冇其他解釋。",
         "prompt_id": "5863beb5-122d-45c1-b32e-1dcd8dab2999", "seed": 20261112},
        {"id": "shot-06", "file": "shot-06.mp4", "duration": 5.083333,
         "caption": "搵唔到可靠來源\n就標示「未證實」\n留言「清單」領取免費版",
         "prompt_id": "6bd3aff6-ad05-46cb-bc2b-ef92f19a81f0", "seed": 20261113},
    ]
    recipe.CANTONESE_NARRATION = (
        "你有冇試過，見到人工智能講得好肯定，就以為一定係真？"
        "記住，講得流暢唔代表有證據。"
        "第一，拆開人名、日期同數字，逐樣核實。"
        "第二，返去睇原始來源，唔好只睇轉述。"
        "第三，主動搵反面證據。"
        "搵唔到可靠來源，就標示未證實。"
    )

    result = recipe.build(ROOT, OUTPUT, TITLE, "cantonese_pilot_v2")
    video_path = ROOT / result["mp4"]["path"]
    cover_path = ROOT / result["cover"]["path"]

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

## 共同主題
AI 講得流暢，不等於有證據。把主張拆開、追返原始來源、主動搵反證；未核實就明確標示。

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
可對相近受眾隨機測試兩個一次性測試價，例如 HK$39 與 HK$79；產品內容、流量來源和測試期間保持一致。沒有結帳頁或付款紀錄之前，不宣稱已有付費需求。

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

    weekly_board = """week,topic,hypothesis,hook_variant,platform,post_date,views,avg_watch_seconds,completion_rate,comments,checklist_deliveries,qualified_leads,paid_orders,refunds,net_revenue,cost,learning
2026-W48,AI claim verification,Creators need a repeatable evidence log,AI sounds confident but is it true?,, ,,,,,,,,,,,
"""

    write_text("FREE_CHECKLIST.md", free_checklist)
    write_text("PUBLISH_COPY.md", publish_copy)
    write_text("MONETIZATION_PLAN.md", monetization_plan)
    write_text("paid-workflow-kit/README.md", paid_readme)
    write_text("paid-workflow-kit/source-review-rubric.md", source_rubric)
    write_text("paid-workflow-kit/counterevidence-log.md", counter_log)
    write_text("paid-workflow-kit/prompt-pack.md", prompt_pack)
    write_text("paid-workflow-kit/editorial-release-checklist.md", release_checklist)
    write_text("paid-workflow-kit/weekly-content-sprint.csv", weekly_board)

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

    asset_files = [
        p for p in sorted(OUTPUT.rglob("*"))
        if p.is_file() and p.name not in {"monetization-campaign.zip", "DELIVERY-VERIFY.json"}
    ]
    asset_manifest = {
        "schema_version": 1,
        "title": TITLE,
        "status": "testing_not_ready_to_publish",
        "audience": "Cantonese-speaking AI creators, freelancers, consultants and small businesses",
        "offer": "Free AI verification checklist leading to a paid workflow-template beta",
        "video": {
            "path": result["mp4"]["path"],
            "sha256": result["mp4"]["sha256"],
            "duration_seconds": result["mp4"]["duration_seconds"],
            "width": result["mp4"]["width"],
            "height": result["mp4"]["height"],
            "fps": result["mp4"]["fps"],
            "full_decode": result["mp4"]["full_decode"],
        },
        "voiceover": result.get("voiceover"),
        "subtitle_review": result.get("subtitle_review"),
        "product_quality_gate": (result.get("product_validation") or {}).get("quality_gate"),
        "publish_api_called": False,
        "assets": [
            {"path": p.relative_to(OUTPUT).as_posix(), "bytes": p.stat().st_size, "sha256": sha256(p)}
            for p in asset_files
        ],
        "revenue": {"status": "not_observed", "actual_revenue": None},
        "disclosures": [
            "AI-generated Wan footage and synthetic Cantonese narration require platform review/disclosure.",
            "The current Cantonese TTS uses one neutral default voice; human listening approval is still required.",
            "The video is a lead-generation short, not evidence of creator-fund eligibility or earnings.",
        ],
    }
    manifest_path = write_text(
        "CAMPAIGN-MANIFEST.json",
        json.dumps(asset_manifest, ensure_ascii=False, indent=2) + "\n",
    )

    archive_path = OUTPUT / "monetization-campaign.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(OUTPUT.rglob("*")):
            if path.is_file() and path != archive_path and path.name != "DELIVERY-VERIFY.json":
                archive.write(path, path.relative_to(OUTPUT).as_posix())
    with zipfile.ZipFile(archive_path) as archive:
        bad_member = archive.testzip()
        members = archive.namelist()
        if bad_member is not None:
            raise RuntimeError(f"campaign ZIP failed CRC check: {bad_member}")
        if len(members) < 15:
            raise RuntimeError(f"campaign ZIP unexpectedly contains only {len(members)} files")

    verification = {
        "status": "validated",
        "video_sha256": sha256(video_path),
        "cover_sha256": sha256(cover_path),
        "campaign_zip": archive_path.relative_to(ROOT).as_posix(),
        "campaign_zip_bytes": archive_path.stat().st_size,
        "campaign_zip_sha256": sha256(archive_path),
        "zip_members": len(members),
        "zip_crc": "passed",
        "campaign_manifest": manifest_path.relative_to(ROOT).as_posix(),
        "product_status": (result.get("product") or {}).get("status"),
        "ready_to_publish": False,
        "publish_api_called": False,
        "actual_revenue": None,
    }
    (OUTPUT / "DELIVERY-VERIFY.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(verification, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
