"""Build a five-platform social draft kit bound to the exact v7 video hashes."""
from __future__ import annotations
import hashlib,json,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PILOT=ROOT/"data/monetization/first-cantonese-pilot-2026-11-24-v7"
OUT=ROOT/"outputs/creator_pilot_20261124/social-content-kit-v3"
def sha(p):
 h=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1024*1024),b""):h.update(b)
 return h.hexdigest()
def writej(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
def main():
 if OUT.exists():raise FileExistsError(OUT)
 manifest=json.loads((PILOT/"manifest.json").read_text(encoding="utf-8"))
 for name,digest in manifest["files"].items():
  p=PILOT/name
  if name!="README.md" and (not p.is_file() or sha(p)!=digest):raise RuntimeError("v7 manifest mismatch: "+name)
 pkg=PILOT/"delivery-package.zip"
 with zipfile.ZipFile(pkg) as z:
  if z.testzip():raise RuntimeError("v7 package CRC failed")
  for name,digest in manifest["files"].items():
   if hashlib.sha256(z.read(name)).hexdigest()!=digest:raise RuntimeError("v7 package member mismatch: "+name)
 vertical=manifest["videos"]["vertical"]
 platforms=[
 {"platform":"YouTube Shorts","id":"youtube_shorts","title":"AI 講得快，不代表答得啱？三步查證法｜粵語旁白","caption":"AI 回答得流暢、有自信，不代表每個細節都有證據。記住三步：拆開主張、查原始來源、主動尋找反證。找不到可靠來源，就標記「未證實」。","hashtags":["#AI","#人工智能","#事實核查","#知識科普"],"cta":"訂閱並留言：你會先核查哪一項？"},
 {"platform":"TikTok","id":"tiktok","title":"AI講得咁肯定，就一定真？記住呢三步","caption":"唔好將流暢表達當成事實證明。拆開主張、查原始來源、主動搵反證。搵唔到可靠來源，就講清楚：未證實。","hashtags":["#AI","#人工智能","#查證","#粵語"],"cta":"留言講下你遇過邊種 AI 一本正經講錯嘢。"},
 {"platform":"Instagram Reels","id":"instagram_reels","title":"AI講得快 ≠ 答得啱｜三步查證","caption":"Before you trust an AI answer: break down the claim, check the original source, and look for counter-evidence. If evidence is missing, label it unverified.","hashtags":["#AI","#FactChecking","#DigitalLiteracy","#人工智能"],"cta":"收藏這份三步清單，下次核查時再打開。"},
 {"platform":"哔哩哔哩","id":"bilibili","title":"AI讲得好肯定，就一定是真？记住这三步","caption":"别把流畅表达当成事实证明。拆分主张、查原始来源、主动寻找反证。找不到可靠来源，就明确标记「未证实」。","hashtags":["#人工智能","#事实核验","#信息素养","#知识科普"],"cta":"收藏本期清单，并在评论区补充你的查证方法。"},
 {"platform":"小红书","id":"xiaohongshu","title":"AI说得越肯定，就越可信吗？3步查证法","caption":"AI回答得很自信，不等于它说的就是真的。遇到重要信息，先做三步：拆开具体主张、查原始来源、主动找反面证据。找不到可靠来源，就标记「未证实」。","hashtags":["#AI工具","#信息差","#事实核查","#知识管理"],"cta":"收藏这份清单，下次核验时照着做。"}
 ]
 disclosure="本片画面包含 AI 生成素材，旁白为本地 AI 合成语音；发布前须按目标平台当期规则披露。"
 source="参考 NIST AI 600-1（2024）关于生成式 AI confabulation、来源与引用核验的风险管理建议。三步流程是编辑实践建议，不代表 NIST 认证、背书或提供准确率统计。"
 blockers=["模型输出、Wan 2.1 画面与背景音频商业使用权尚未完成审查","本地粤语语音为中性声线，尚未获用户确认，不等同成熟女声","最终混音 ASR 尚未运行；独立旁白 CER 不代表混音识别结果","人工完整试听、字幕校对与手机画面审查待完成","各平台 AI 内容披露与上传规格待逐个平台核对","账号授权未连接；未调用发布 API；无排程"]
 data={"schema":"muse.social-content-kit.v3","campaign":"muse-ai-verification-2026-11","status":"draft_only",
  "source_video":{"path":"data/monetization/first-cantonese-pilot-2026-11-24-v7/video-9x16.mp4","sha256":vertical["sha256"],"voiceover_used":True,"subtitle_track":"mov_text"},
  "horizontal_video":{"path":"data/monetization/first-cantonese-pilot-2026-11-24-v7/video-16x9.mp4","sha256":manifest["videos"]["horizontal"]["sha256"]},
  "source_attribution":source,"ai_disclosure":disclosure,"release_blockers":blockers,
  "platforms":[{**x,"description":x["caption"]+"\n\n"+source+"\n\n"+disclosure,"schedule":{"status":"not_scheduled","publish_at":None},"release_state":"draft_only","publish_api_called":False} for x in platforms],
  "tracking":{"utm_created":False,"destination_url":None,"analytics_connected":False,"revenue_status":"not_observed"},
  "orders":0,"settled_revenue":0,"can_publish":False,"published":False}
 OUT.mkdir(parents=True)
 writej(OUT/"social-content-kit.json",data)
 lines=["# Muse 社交平台发布草稿 v3","","状态：draft_only；与 v7 实际旁白成片 SHA-256 绑定，不可直接发布。","",
  "竖版视频路径："+data["source_video"]["path"],"","竖版 SHA-256："+vertical["sha256"],"",
  "旁白状态：used_in_video；软字幕：mov_text（可选字幕轨）。","","## 内容来源与披露","",source,"",disclosure,"","## 发布前阻塞项",""]+[f"- {x}" for x in blockers]
 for x in data["platforms"]:
  lines += ["",f"## {x['platform']}","",f"**标题**：{x['title']}","",x["caption"],"",
   "**标签**："+ " ".join(x["hashtags"]),"",f"**CTA**：{x['cta']}","",
   "**简介补充**：",source,disclosure,"","**排程**：未安排；根据账号实际受众数据人工决定。","",
   "**发布状态**：草稿；账号未授权；未调用发布 API。"]
 (OUT/"social-content-kit.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
 (OUT/"README.md").write_text("# Muse Social Content Kit v3\n\nFive platform-specific draft copies are bound to the exact v7 narrated video hashes. No account login, upload, scheduling, analytics, payment or revenue action was performed. All release blockers remain explicit.\n",encoding="utf-8")
 files={}
 for p in sorted(OUT.iterdir()):
  if p.is_file() and p.name not in {"manifest.json","social-content-kit.zip","package.sha256"}:files[p.name]=sha(p)
 pm={"schema":"muse.social-content-kit-manifest.v1","source_video_sha256":vertical["sha256"],
  "source_video_voiceover_used":True,"status":"draft_only","can_publish":False,"files":files}
 writej(OUT/"manifest.json",pm)
 zpath=OUT/"social-content-kit.zip"
 with zipfile.ZipFile(zpath,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
  for p in sorted(OUT.iterdir()):
   if p.is_file() and p.name not in {"social-content-kit.zip","package.sha256"}:z.write(p,p.name)
 with zipfile.ZipFile(zpath) as z:
  if z.testzip():raise RuntimeError("social kit ZIP CRC failed")
  if set(z.namelist())!=set(files)|{"manifest.json"}:raise RuntimeError("social kit ZIP member set mismatch")
  for name,digest in files.items():
   if hashlib.sha256(z.read(name)).hexdigest()!=digest:raise RuntimeError("social kit ZIP hash mismatch: "+name)
  if json.loads(z.read("manifest.json"))!=pm:raise RuntimeError("embedded social manifest mismatch")
 digest=sha(zpath)
 (OUT/"package.sha256").write_text(f"{digest}  social-content-kit.zip\n",encoding="ascii")
 print(json.dumps({"output":str(OUT.relative_to(ROOT)),"platforms":[x["platform"] for x in platforms],
  "source_video_sha256":vertical["sha256"],"social_kit_sha256":digest,"zip_crc":"passed",
  "release_state":"draft_only","can_publish":False,"blockers":len(blockers)},ensure_ascii=False,indent=2))
if __name__=="__main__":main()
