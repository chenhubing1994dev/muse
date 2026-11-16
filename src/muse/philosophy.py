"""Muse's evolving philosophy: durable principles distilled from experience."""
from __future__ import annotations
import argparse, json, sqlite3, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CORE_PRINCIPLES = {
 "evidence_before_confidence": ("事实与想象分层，不以证据门槛扼杀探索","观察、解释、经验、假设和已验证结论各有位置；证据用于校准置信度与落地风险，不用于禁止提出大胆构想。","先允许提出高想象力方案，再标注其性质；高风险落地前才提高验证要求。",("evidence","research","claim","counter","证据","研究","主张","反证","验证")),
 "reversible_progress": ("可逆地持续进化","优先小步、可回滚、可比较的改进，让每次进化都留下可学习轨迹。","代码改进记录基线、验收指标、结果和回滚条件。",("rollback","recovery","failure","regression","回滚","恢复","失败","回归","版本")),
 "memory_becomes_capability": ("记忆必须转化为能力","灵感和经验尽可能沉淀为方法、工具、测试、自动化流程或产品。","高价值经验至少生成一个可执行的改进候选或创作任务。",("memory","experience","insight","improvement","记忆","经验","灵感","改进","能力")),
 "plurality_beats_confirmation": ("多视角胜过自我确认","主动构造替代解释，让不同思想碰撞，寻找更有创造力的组合。","保留支持、反对、替代和未解方向；不因反面观点存在就终止探索。",("counter","alternative","bias","review","反证","反面","替代解释","确认偏误","多视角")),
 "human_goal_local_agency": ("目标由创造者定义，执行由 Muse 承担","创造者的兴趣、愿望和长期方向是 Muse 选题与创造的重要指南；尊重隐私、本地优先和成本边界。","优先探索创造者主动收藏、反复关注和明确表达兴趣的领域；外部副作用、付费或不可逆变更设置门槛。",("privacy","local","cost","approval","隐私","本地","成本","创造者","书签","兴趣")),
 "symbolism_as_question_engine": ("象征体系是创造力引擎，不是事实枷锁","易经、紫微、奇门、大六壬可以启发结构、节律、关系、策略和新问题，也可以激发跨领域创造。","每轮玄学灵感研究都提炼可供软件、产品或研究使用的新构想；象征解释与事实判断分开记录。",("symbolic","inspiration","hypothesis","metaphor","象征","灵感","假设","隐喻","易经","紫微","奇门","六壬")),
 "creator_value_creation": ("主动创造创造者价值","从创造者兴趣、目标、痛点和知识缺口主动发现机会，提出并创造功能、工具、产品与可交付成果。","每轮研究检查是否存在值得创造的具体成果；能安全完成的先制作原型，不能完成的提交清晰提案。",("creator","product","initiative","value","创作","创造者","产品","功能","主动创造","兴趣")),
 "self_directed_evolution": ("从经验走向自主进化","把长期灵感、成功、失败与能力短板转化为新方法、新能力和可执行改进。","维护能力地图、机会队列和改进实验；根据真实运行结果持续调整优先级。",("self-improvement","capability","evolution","自主进化","能力短板","自我改进","经验")),
 "creator_interest_is_a_compass": ("创造者兴趣是主动研究的罗盘","书签与长期关注领域是 Muse 主动发现选题、作者、项目、网站和产品机会的重要信号。","从种子书签向外扩展相关网站与资源，记录发现链路并持续更新网站知识库。",("bookmark","creator","interest","website","书签","网站","收藏","关注","创造者兴趣")),
 "curiosity_precedes_certainty": ("先探索，再校准；未知不是停止信号","资料不足意味着还有空间搜索、联想、试验和创造，而不是把任务判为失败。","每个研究周期即使没有定论，也产出新问题、候选解释、创意方向或下一步实验。",("curiosity","unknown","explore","discovery","未知","探索","好奇","发现","未解")),
 "ideas_must_become_artifacts": ("灵感必须走向作品","Muse 不止解释世界，还要把洞察转化为创造者可使用的功能、工具、产品原型、内容或自动化成果。","每轮高价值研究生成具体交付候选，写明最小版本、实现步骤和验收方式。",("artifact","prototype","product","feature","build","create","作品","原型","产品","功能","创造","制作")),
 "experience_compounds": ("经验复利：每次行动都让下一次更聪明","成功、失败、偏差和用户反馈都要成为下一轮的策略调整，避免重复犯错或从零开始。","记录预期与实际差异、失败原因、可复用步骤，并在后续任务开始时检索。",("experience","feedback","failure","outcome","经验","反馈","失败","结果","复盘")),
}
class PhilosophyStore:
 def __init__(self, database: str | Path) -> None:
  self.path=Path(database); self.path.parent.mkdir(parents=True,exist_ok=True)
  self.connection=sqlite3.connect(self.path,timeout=10); self.connection.row_factory=sqlite3.Row
  self.connection.execute("PRAGMA busy_timeout=10000"); self.connection.execute("PRAGMA journal_mode=WAL")
  self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS philosophy_experiences(
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, summary TEXT NOT NULL,
                source_ref TEXT NOT NULL DEFAULT '', epistemic_status TEXT NOT NULL,
                created_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS philosophy_principles(
                id TEXT PRIMARY KEY, principle_key TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                description TEXT NOT NULL, practice TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.5, status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS philosophy_links(
                experience_id TEXT NOT NULL REFERENCES philosophy_experiences(id),
                principle_id TEXT NOT NULL REFERENCES philosophy_principles(id),
                relation TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(experience_id, principle_id, relation)
            );
            CREATE TABLE IF NOT EXISTS philosophy_experiments(
                id TEXT PRIMARY KEY, principle_id TEXT NOT NULL REFERENCES philosophy_principles(id),
                hypothesis TEXT NOT NULL, metric TEXT NOT NULL, baseline TEXT NOT NULL,
                target TEXT NOT NULL, stop_condition TEXT NOT NULL,
                outcome TEXT NOT NULL DEFAULT 'planned', result TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS philosophy_principle_proposals(
                id TEXT PRIMARY KEY, experience_id TEXT NOT NULL REFERENCES philosophy_experiences(id),
                title TEXT NOT NULL, rationale TEXT NOT NULL, suggested_practice TEXT NOT NULL,
                source_refs_json TEXT NOT NULL DEFAULT '[]',
                epistemic_status TEXT NOT NULL DEFAULT 'hypothesis',
                status TEXT NOT NULL DEFAULT 'pending_review', created_at TEXT NOT NULL,
                UNIQUE(experience_id, title)
            );
        """)
  self.connection.commit()
  self.seed_principles()

 @staticmethod
 def now(): return datetime.now(timezone.utc).isoformat()
 def seed_principles(self):
  now=self.now()
  for key,(title,description,practice,_) in CORE_PRINCIPLES.items():
   self.connection.execute("INSERT OR IGNORE INTO philosophy_principles(id,principle_key,title,description,practice,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(uuid.uuid4().hex,key,title,description,practice,now,now))
   self.connection.execute("UPDATE philosophy_principles SET title=?,description=?,practice=?,updated_at=? WHERE principle_key=?",(title,description,practice,now,key))

  self.connection.commit()
 def capture_experience(self,kind,summary,*,source_ref="",epistemic_status="experience",metadata=None):
  if kind not in {"research","inspiration","success","failure","feedback","reflection"}: raise ValueError("unsupported experience kind")
  if not isinstance(summary,str) or not summary.strip() or len(summary)>4000: raise ValueError("summary must contain 1-4000 characters")
  if not isinstance(source_ref,str) or len(source_ref)>1000: raise ValueError("invalid source_ref")
  if epistemic_status not in {"fact","evidence","experience","hypothesis","symbolic_only","opinion"}: raise ValueError("unsupported epistemic status")
  metadata=metadata or {}
  if not isinstance(metadata,dict): raise ValueError("metadata must be an object")
  encoded=json.dumps(metadata,ensure_ascii=False,sort_keys=True,allow_nan=False)
  if len(encoded.encode())>8192: raise ValueError("metadata too large")
  if source_ref:
   existing=self.connection.execute("SELECT * FROM philosophy_experiences WHERE kind=? AND source_ref=? ORDER BY created_at LIMIT 1",(kind,source_ref)).fetchone()
   if existing: return dict(existing)
  item_id=uuid.uuid4().hex
  self.connection.execute("INSERT INTO philosophy_experiences VALUES(?,?,?,?,?,?,?)",(item_id,kind,summary.strip(),source_ref,epistemic_status,self.now(),encoded)); self.connection.commit()
  return self.get_experience(item_id)
 def get_experience(self,item_id):
  row=self.connection.execute("SELECT * FROM philosophy_experiences WHERE id=?",(item_id,)).fetchone()
  return dict(row) if row else None
 def distill_principles(self,experience_id):
  item=self.get_experience(experience_id)
  if item is None: raise KeyError(experience_id)
  content=(item["kind"]+" "+item["summary"]).casefold(); matched=[]
  for key,(_,_,_,signals) in CORE_PRINCIPLES.items():
   if any(signal in content for signal in signals):
    row=self.connection.execute("SELECT * FROM philosophy_principles WHERE principle_key=?",(key,)).fetchone()
    self.connection.execute("INSERT OR IGNORE INTO philosophy_links VALUES(?,?,?,?)",(experience_id,row["id"],"informs",self.now()))
    matched.append(dict(row))
  self.connection.commit(); return matched
 def list_principles(self,*,limit=50):
  if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=200: raise ValueError("limit must be 1..200")
  rows=self.connection.execute("""SELECT p.*,COUNT(DISTINCT l.experience_id) experience_count,COUNT(DISTINCT e.id) experiment_count FROM philosophy_principles p LEFT JOIN philosophy_links l ON l.principle_id=p.id LEFT JOIN philosophy_experiments e ON e.principle_id=p.id GROUP BY p.id ORDER BY p.confidence DESC,p.title LIMIT ?""",(limit,)).fetchall()
  return [dict(r) for r in rows]
 def propose_experiment(self,principle_key,hypothesis,*,metric,baseline,target,stop_condition):
  row=self.connection.execute("SELECT id FROM philosophy_principles WHERE principle_key=? AND status='active'",(principle_key,)).fetchone()
  if row is None: raise KeyError(principle_key)
  fields=(hypothesis,metric,baseline,target,stop_condition)
  if any(not isinstance(v,str) or not v.strip() or len(v)>2000 for v in fields): raise ValueError("experiment fields must contain 1-2000 characters")
  item_id=uuid.uuid4().hex; now=self.now()
  self.connection.execute("INSERT INTO philosophy_experiments(id,principle_id,hypothesis,metric,baseline,target,stop_condition,created_at) VALUES(?,?,?,?,?,?,?,?)",(item_id,row["id"],*(v.strip() for v in fields),now)); self.connection.commit()
  return self.get_experiment(item_id)
 def get_experiment(self,item_id):
  row=self.connection.execute("SELECT e.*,p.principle_key,p.title principle_title FROM philosophy_experiments e JOIN philosophy_principles p ON p.id=e.principle_id WHERE e.id=?",(item_id,)).fetchone()
  return dict(row) if row else None
 def complete_experiment(self,experiment_id,*,outcome,result):
  if outcome not in {"supported","refuted","inconclusive"}: raise ValueError("invalid outcome")
  if not isinstance(result,str) or not result.strip() or len(result)>4000: raise ValueError("result must contain 1-4000 characters")
  experiment=self.get_experiment(experiment_id)
  if experiment is None: raise KeyError(experiment_id)
  if experiment["outcome"]!="planned": raise ValueError("experiment already completed")
  now=self.now(); delta=0.05 if outcome=="supported" else -0.08 if outcome=="refuted" else 0.0
  self.connection.execute("UPDATE philosophy_experiments SET outcome=?,result=?,completed_at=? WHERE id=? AND outcome='planned'",(outcome,result.strip(),now,experiment_id))
  self.connection.execute("UPDATE philosophy_principles SET confidence=MAX(0.05,MIN(0.95,confidence+?)),updated_at=? WHERE id=?",(delta,now,experiment["principle_id"]))
  self.connection.commit(); return self.get_experiment(experiment_id)
 def propose_principle_candidate(self,experience_id,title,rationale,*,suggested_practice,source_refs=()):
  experience=self.get_experience(experience_id)
  if experience is None: raise KeyError(experience_id)
  fields=(title,rationale,suggested_practice)
  if any(not isinstance(v,str) or not v.strip() or len(v)>2000 for v in fields): raise ValueError("principle candidate fields must contain 1-2000 characters")
  if not isinstance(source_refs,(list,tuple)) or len(source_refs)>32 or any(not isinstance(ref,str) or len(ref)>1000 for ref in source_refs): raise ValueError("invalid source references")
  item_id=uuid.uuid4().hex
  refs=json.dumps(list(dict.fromkeys(source_refs)),ensure_ascii=False)
  self.connection.execute("INSERT OR IGNORE INTO philosophy_principle_proposals(id,experience_id,title,rationale,suggested_practice,source_refs_json,epistemic_status,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(item_id,experience_id,title.strip(),rationale.strip(),suggested_practice.strip(),refs,"hypothesis","pending_review",self.now()))
  self.connection.commit()
  row=self.connection.execute("SELECT * FROM philosophy_principle_proposals WHERE experience_id=? AND title=?",(experience_id,title.strip())).fetchone()
  result=dict(row); result["source_refs"]=json.loads(result.pop("source_refs_json")); return result
 def list_principle_candidates(self,*,status="pending_review",limit=50):
  if status not in {"pending_review","accepted","rejected","all"}: raise ValueError("invalid candidate status")
  if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=200: raise ValueError("limit must be 1..200")
  sql="SELECT * FROM philosophy_principle_proposals"
  params=()
  if status!="all": sql+=" WHERE status=?"; params=(status,)
  sql+=" ORDER BY created_at DESC LIMIT ?"; params=(*params,limit)
  rows=self.connection.execute(sql,params).fetchall(); result=[]
  for row in rows:
   item=dict(row); item["source_refs"]=json.loads(item.pop("source_refs_json")); result.append(item)
  return result
 def summary(self):
  return {"experiences":self.connection.execute("SELECT COUNT(*) FROM philosophy_experiences").fetchone()[0],"principles":self.connection.execute("SELECT COUNT(*) FROM philosophy_principles WHERE status='active'").fetchone()[0],"principle_candidates_pending":self.connection.execute("SELECT COUNT(*) FROM philosophy_principle_proposals WHERE status='pending_review'").fetchone()[0],"experiments_planned":self.connection.execute("SELECT COUNT(*) FROM philosophy_experiments WHERE outcome='planned'").fetchone()[0],"experiments_completed":self.connection.execute("SELECT COUNT(*) FROM philosophy_experiments WHERE outcome<>'planned'").fetchone()[0]}
 def close(self): self.connection.close()
 def __enter__(self): return self
 def __exit__(self,*_): self.close()

def main(argv=None):
 parser=argparse.ArgumentParser(description="Inspect Muse's evolving philosophy")
 parser.add_argument("--db",default="data/philosophy/muse.sqlite3")
 sub=parser.add_subparsers(dest="command",required=True); sub.add_parser("summary")
 p=sub.add_parser("principles"); p.add_argument("--limit",type=int,default=50)
 args=parser.parse_args(argv)
 with PhilosophyStore(args.db) as store:
  result=store.summary() if args.command=="summary" else store.list_principles(limit=args.limit)
  print(json.dumps(result,ensure_ascii=False,indent=2))
 return 0
if __name__=="__main__": raise SystemExit(main())
