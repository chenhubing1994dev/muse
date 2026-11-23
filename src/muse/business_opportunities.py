"""Evidence-aware local business opportunity ledger for Muse."""
from __future__ import annotations
import argparse, hashlib, json, re, sqlite3, uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

DIMENSIONS=("demand_signal","willingness_to_pay","delivery_fit","competition_gap","repeatability","risk")
WEIGHTS={"demand_signal":25,"willingness_to_pay":25,"delivery_fit":20,"competition_gap":15,"repeatability":15}
EVIDENCE_TYPES={"official_source","customer_interview","market_data","competitor_analysis","creator_observation","estimate"}
EPISTEMIC={"observed","reported","assumption"}
STATUSES={"candidate","researching","pilot_ready","testing","validated","rejected","parked"}


def score_opportunity(ratings):
    """Heuristic ranking only; never a probability or revenue forecast."""
    if not isinstance(ratings,dict) or set(ratings)!=set(DIMENSIONS):
        raise ValueError("ratings must contain exactly the six supported dimensions")
    for key,value in ratings.items():
        if isinstance(value,bool) or not isinstance(value,int) or not 1<=value<=5:
            raise ValueError(f"{key} must be an integer from 1 to 5")
    base=sum(ratings[k]/5*w for k,w in WEIGHTS.items())
    return round(max(0.0,min(100.0,base-(ratings["risk"]-1)/4*15)),2)


def _bounded(value,name,limit=2000):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:
        raise ValueError(f"{name} must be non-empty text of at most {limit} characters")
    return value.strip()


def _url(value):
    if not value: return ""
    if not isinstance(value,str) or len(value)>2048: raise ValueError("source_url must be at most 2048 characters")
    parsed=urlsplit(value)
    if parsed.scheme not in {"http","https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("source_url must be http(s) without embedded credentials")
    return value


class BusinessOpportunityStore:
    """SQLite ledger for opportunity candidates, evidence and lifecycle events."""
    def __init__(self,database):
        path=Path(database); path.parent.mkdir(parents=True,exist_ok=True)
        self.connection=sqlite3.connect(path,timeout=10)
        self.connection.row_factory=sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS business_opportunities(
          id TEXT PRIMARY KEY,idempotency_key TEXT NOT NULL UNIQUE,title TEXT NOT NULL,
          customer_segment TEXT NOT NULL,customer_problem TEXT NOT NULL,proposed_offer TEXT NOT NULL,
          validation_test TEXT NOT NULL,success_signal TEXT NOT NULL,currency TEXT NOT NULL CHECK(length(currency)=3),
          estimated_price_minor INTEGER NOT NULL CHECK(estimated_price_minor>=0),
          estimated_cost_minor INTEGER NOT NULL CHECK(estimated_cost_minor>=0),
          ratings_json TEXT NOT NULL,score REAL NOT NULL,status TEXT NOT NULL,
          created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_business_opportunity_queue ON business_opportunities(status,score DESC,created_at);
        CREATE TABLE IF NOT EXISTS business_opportunity_evidence(
          id TEXT PRIMARY KEY,opportunity_id TEXT NOT NULL REFERENCES business_opportunities(id) ON DELETE RESTRICT,
          idempotency_key TEXT NOT NULL,evidence_type TEXT NOT NULL,epistemic_status TEXT NOT NULL,
          source_url TEXT NOT NULL DEFAULT '',summary TEXT NOT NULL,captured_at TEXT NOT NULL,
          UNIQUE(opportunity_id,idempotency_key));
        CREATE TRIGGER IF NOT EXISTS business_evidence_no_update BEFORE UPDATE ON business_opportunity_evidence
          BEGIN SELECT RAISE(ABORT,'opportunity evidence is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS business_evidence_no_delete BEFORE DELETE ON business_opportunity_evidence
          BEGIN SELECT RAISE(ABORT,'opportunity evidence is append-only'); END;
        CREATE TABLE IF NOT EXISTS business_opportunity_events(
          sequence INTEGER PRIMARY KEY AUTOINCREMENT,opportunity_id TEXT NOT NULL REFERENCES business_opportunities(id),
          event_type TEXT NOT NULL,from_status TEXT,to_status TEXT,details_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS business_events_no_update BEFORE UPDATE ON business_opportunity_events
          BEGIN SELECT RAISE(ABORT,'opportunity events are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS business_events_no_delete BEFORE DELETE ON business_opportunity_events
          BEGIN SELECT RAISE(ABORT,'opportunity events are append-only'); END;
        """)
        self.connection.commit()

    @staticmethod
    def _now(): return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _decode(row):
        item=dict(row); item["ratings"]=json.loads(item.pop("ratings_json")); return item

    def _event(self,item_id,event_type,from_status=None,to_status=None,details=None):
        self.connection.execute(
          "INSERT INTO business_opportunity_events(opportunity_id,event_type,from_status,to_status,details_json,created_at) VALUES(?,?,?,?,?,?)",
          (item_id,event_type,from_status,to_status,json.dumps(details or {},sort_keys=True),self._now()))

    def create(self,*,idempotency_key,title,customer_segment,customer_problem,proposed_offer,
               validation_test,success_signal,ratings,currency="USD",estimated_price_minor=0,estimated_cost_minor=0):
        if not isinstance(idempotency_key,str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}",idempotency_key):
            raise ValueError("invalid idempotency_key")
        values={"title":_bounded(title,"title",200),"customer_segment":_bounded(customer_segment,"customer_segment",500),
          "customer_problem":_bounded(customer_problem,"customer_problem"),"proposed_offer":_bounded(proposed_offer,"proposed_offer"),
          "validation_test":_bounded(validation_test,"validation_test"),"success_signal":_bounded(success_signal,"success_signal",1000)}
        if not isinstance(currency,str) or not re.fullmatch(r"[A-Z]{3}",currency): raise ValueError("invalid currency")
        for name,value in (("estimated_price_minor",estimated_price_minor),("estimated_cost_minor",estimated_cost_minor)):
            if isinstance(value,bool) or not isinstance(value,int) or value<0: raise ValueError(f"{name} must be non-negative integer")
        score=score_opportunity(ratings)
        old=self.connection.execute("SELECT * FROM business_opportunities WHERE idempotency_key=?",(idempotency_key,)).fetchone()
        expected={**values,"currency":currency,"estimated_price_minor":estimated_price_minor,
          "estimated_cost_minor":estimated_cost_minor,"ratings":ratings,"score":score}
        if old:
            decoded=self._decode(old)
            if any(decoded[k]!=v for k,v in expected.items()): raise ValueError("idempotency key conflicts with existing opportunity")
            return self.get(decoded["id"])
        item_id,now=uuid.uuid4().hex,self._now()
        self.connection.execute("""INSERT INTO business_opportunities
          (id,idempotency_key,title,customer_segment,customer_problem,proposed_offer,validation_test,success_signal,
           currency,estimated_price_minor,estimated_cost_minor,ratings_json,score,status,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'candidate',?,?)""",
          (item_id,idempotency_key,values["title"],values["customer_segment"],values["customer_problem"],
           values["proposed_offer"],values["validation_test"],values["success_signal"],currency,
           estimated_price_minor,estimated_cost_minor,json.dumps(ratings,sort_keys=True),score,now,now))
        self._event(item_id,"created",to_status="candidate"); self.connection.commit()
        return self.get(item_id)

    def get(self,opportunity_id):
        row=self.connection.execute("SELECT * FROM business_opportunities WHERE id=?",(opportunity_id,)).fetchone()
        if row is None: raise KeyError(opportunity_id)
        item=self._decode(row); item["evidence"]=self.list_evidence(opportunity_id=opportunity_id)
        kinds={e["evidence_type"] for e in item["evidence"] if e["epistemic_status"]!="assumption"}
        item["evidence_summary"]={"count":len(item["evidence"]),
          "assumptions":sum(e["epistemic_status"]=="assumption" for e in item["evidence"]),
          "observed":sum(e["epistemic_status"]=="observed" for e in item["evidence"]),
          "reported":sum(e["epistemic_status"]=="reported" for e in item["evidence"]),
          "validation_state":"evidence_supported_candidate" if len(kinds)>=2 and bool(kinds & {"customer_interview","market_data","official_source"}) else "needs_more_evidence"}
        return item

    def add_evidence(self,opportunity_id,*,idempotency_key,evidence_type,epistemic_status,summary,source_url=""):
        if evidence_type not in EVIDENCE_TYPES or epistemic_status not in EPISTEMIC: raise ValueError("unsupported evidence classification")
        if not isinstance(idempotency_key,str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}",idempotency_key): raise ValueError("invalid evidence idempotency_key")
        summary,source_url=_bounded(summary,"summary"),_url(source_url)
        if evidence_type in {"official_source","market_data","competitor_analysis"} and not source_url:
            raise ValueError("public-source evidence requires source_url")
        if not self.connection.execute("SELECT 1 FROM business_opportunities WHERE id=?",(opportunity_id,)).fetchone(): raise KeyError(opportunity_id)
        old=self.connection.execute("SELECT * FROM business_opportunity_evidence WHERE opportunity_id=? AND idempotency_key=?",(opportunity_id,idempotency_key)).fetchone()
        if old:
            if any(old[k]!=v for k,v in {"evidence_type":evidence_type,"epistemic_status":epistemic_status,"summary":summary,"source_url":source_url}.items()):
                raise ValueError("evidence idempotency key conflict")
            return dict(old)
        evidence_id,now=uuid.uuid4().hex,self._now()
        self.connection.execute("""INSERT INTO business_opportunity_evidence
          (id,opportunity_id,idempotency_key,evidence_type,epistemic_status,source_url,summary,captured_at)
          VALUES(?,?,?,?,?,?,?,?)""",(evidence_id,opportunity_id,idempotency_key,evidence_type,epistemic_status,source_url,summary,now))
        self._event(opportunity_id,"evidence_added",details={"evidence_id":evidence_id,"evidence_type":evidence_type,"epistemic_status":epistemic_status})
        self.connection.commit()
        return dict(self.connection.execute("SELECT * FROM business_opportunity_evidence WHERE id=?",(evidence_id,)).fetchone())

    def list_evidence(self,*,opportunity_id,limit=100):
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=200: raise ValueError("limit must be 1..200")
        return [dict(r) for r in self.connection.execute(
          "SELECT * FROM business_opportunity_evidence WHERE opportunity_id=? ORDER BY captured_at,id LIMIT ?",
          (opportunity_id,limit)).fetchall()]

    def set_status(self,opportunity_id,status):
        if status not in STATUSES: raise ValueError("unsupported opportunity status")
        row=self.connection.execute("SELECT status FROM business_opportunities WHERE id=?",(opportunity_id,)).fetchone()
        if row is None: raise KeyError(opportunity_id)
        if row["status"]!=status:
            now=self._now()
            self.connection.execute("UPDATE business_opportunities SET status=?,updated_at=? WHERE id=?",(status,now,opportunity_id))
            self._event(opportunity_id,"status_changed",row["status"],status); self.connection.commit()
        return self.get(opportunity_id)

    def shortlist(self,*,limit=3):
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=20: raise ValueError("limit must be 1..20")
        rows=self.connection.execute("""SELECT id FROM business_opportunities
          WHERE status IN ('candidate','researching','pilot_ready','testing')
          ORDER BY score DESC,created_at ASC LIMIT ?""",(limit,)).fetchall()
        return [self.get(r["id"]) for r in rows]

    def promote_to_initiative(self,opportunity_id,*,initiative_database):
        """Create an idempotent draft in Muse's existing Creator Initiative ledger."""
        opportunity=self.get(opportunity_id)
        if opportunity["status"] in {"rejected","parked"}:
            raise ValueError("rejected or parked opportunities cannot be promoted")
        from muse.initiative_engine import InitiativeStore
        reference=f"business-opportunity:{opportunity_id}"
        title=f"商业试点：{opportunity['title'][:140]} [{opportunity_id[:8]}]"
        concept=json.dumps({
            "schema_version":1,
            "source_kind":"business_opportunity",
            "source_id":opportunity_id,
            "business_plan":{
                "customer_segment":opportunity["customer_segment"],
                "customer_problem":opportunity["customer_problem"],
                "proposed_offer":opportunity["proposed_offer"],
                "currency":opportunity["currency"],
                "estimated_price_minor":opportunity["estimated_price_minor"],
                "estimated_cost_minor":opportunity["estimated_cost_minor"],
                "validation_test":opportunity["validation_test"],
                "success_signal":opportunity["success_signal"],
                "evidence_summary":opportunity["evidence_summary"],
                "evidence_refs":[e["id"] for e in opportunity["evidence"]],
            },
            "creator_brief":{
                "schema_version":1,
                "title":opportunity["title"],
                "video":{
                    "title":opportunity["title"][:120],
                    "prompt":(
                        "Create a polished vertical 9:16 short-form promotional video concept. "
                        f"Show the customer problem: {opportunity['customer_problem']}. "
                        f"Visually demonstrate this offer: {opportunity['proposed_offer']}. "
                        "Use clear cinematic product storytelling, realistic scenes, readable visual hierarchy, "
                        "and a concise call-to-action. Do not invent customer testimonials, prices, or results."
                    )[:1800],
                    "negative_prompt":"fake testimonials, fabricated metrics, unreadable text, watermarks, logos not supplied",
                    "aspect_ratio":"9:16",
                    "duration_seconds":2.0,
                    "steps":8,
                    "narration":{
                        "enabled":True,
                        "language":"zh-HK",
                        "style":"yujie_canto",
                        "segments":3,
                        "research_text":(
                            f"客户问题：{opportunity['customer_problem']}。"
                            f"拟提供方案：{opportunity['proposed_offer']}。"
                            f"验证方式：{opportunity['validation_test']}。"
                            f"成功信号：{opportunity['success_signal']}。"
                            "以上属于待验证试点，不代表已实现结果或客户见证。"
                        )[:12000],
                        "inspiration":"以沉稳、知性、清晰的香港粤语讲解；区分试点构想、估算与已验证事实。",
                        "asr_enabled":False,
                    },
                }
            },
        },ensure_ascii=False,sort_keys=True)
        with InitiativeStore(initiative_database) as initiatives:
            initiative=initiatives.propose(
                title,kind="creative_output",
                creator_value="把经过证据分级的商业机会转化为可审核、可交付的创作试点",
                concept=concept,first_step=opportunity["validation_test"],
                expected_output=opportunity["success_signal"],
                interest_signals=[
                    f"opportunity_score:{opportunity['score']:.2f}",
                    f"demand_signal:{opportunity['ratings']['demand_signal']}/5",
                    f"willingness_to_pay:{opportunity['ratings']['willingness_to_pay']}/5",
                    f"evidence_state:{opportunity['evidence_summary']['validation_state']}",
                ],
                inspiration_refs=[reference],
                capability_gap="付费意愿、真实需求与交付成本仍需试点验证",
                priority=max(0,min(100,round(opportunity["score"]))),
                autonomy_level="draft",
            )
        if reference not in initiative.get("inspiration_refs",[]):
            raise ValueError("an initiative with the same title exists but is linked to another source")
        linked=any(
            json.loads(row["details_json"]).get("initiative_id")==initiative["id"]
            for row in self.connection.execute(
                "SELECT details_json FROM business_opportunity_events WHERE opportunity_id=? AND event_type='initiative_linked'",
                (opportunity_id,),
            ).fetchall()
        )
        if not linked:
            self._event(opportunity_id,"initiative_linked",details={
                "initiative_id":initiative["id"],"reference":reference,"autonomy_level":"draft",
            })
            self.connection.commit()
        return {"opportunity":opportunity,"initiative":initiative,"created_link":not linked}

    def _linked_initiative(self,opportunity_id,*,initiative_database):
        """Resolve the exact linked initiative by durable event ID, with legacy fallback."""
        from muse.initiative_engine import InitiativeStore
        reference=f"business-opportunity:{opportunity_id}"
        rows=self.connection.execute(
            "SELECT details_json FROM business_opportunity_events WHERE opportunity_id=? AND event_type='initiative_linked' ORDER BY sequence DESC",
            (opportunity_id,),
        ).fetchall()
        initiative_ids=[]
        for row in rows:
            details=json.loads(row["details_json"])
            initiative_id=details.get("initiative_id") if isinstance(details,dict) else None
            if isinstance(initiative_id,str) and re.fullmatch(r"[0-9a-f]{32}",initiative_id) and initiative_id not in initiative_ids:
                initiative_ids.append(initiative_id)
        with InitiativeStore(initiative_database) as initiatives:
            for initiative_id in initiative_ids:
                initiative=initiatives.get_by_id(initiative_id)
                if initiative is not None and reference in initiative.get("inspiration_refs",[]):
                    return initiative
            # Compatibility for records created before initiative_linked stored the ID.
            return next((item for item in initiatives.list(limit=200) if reference in item["inspiration_refs"]),None)

    def request_video_approval(self,opportunity_id,*,initiative_database,task_database):
        """Create a Commander approval ticket; never queue GPU work at this stage."""
        from muse.task_queue import TaskQueue
        opportunity=self.get(opportunity_id)
        if opportunity["status"] in {"rejected","parked"}:
            raise ValueError("rejected or parked opportunities cannot request video approval")
        initiative=self._linked_initiative(opportunity_id,initiative_database=initiative_database)
        if initiative is None:
            raise KeyError("promote the opportunity to a Creator Initiative first")
        if initiative["status"] in {"rejected","parked"}:
            raise ValueError("rejected or parked Creator Initiatives cannot request video approval")
        concept=json.loads(initiative["concept"])
        brief=concept.get("creator_brief",{}).get("video") if isinstance(concept,dict) else None
        if not isinstance(brief,dict) or not brief.get("title") or not brief.get("prompt"):
            raise ValueError("initiative does not contain a valid creator_brief.video")
        attempt_id="biz-"+hashlib.sha256(opportunity_id.encode("utf-8")).hexdigest()[:32]
        idem=f"business-video-approval:{opportunity_id}"
        payload={
            "kind":"business_opportunity_video_approval",
            "opportunity_id":opportunity_id,
            "initiative_id":initiative["id"],
            "attempt_id":attempt_id,
            "video_brief_sha256":hashlib.sha256(json.dumps(brief,sort_keys=True,ensure_ascii=False).encode("utf-8")).hexdigest(),
            "video_brief":brief,
            "approval_scope":"one_video_attempt_only",
        }
        with TaskQueue(task_database) as queue:
            ticket=queue.enqueue(
                f"审核商业机会视频样片：{opportunity['title'][:100]}",
                payload,priority=max(0,min(100,round(opportunity["score"]))),
                recovery_policy="manual",idempotency_key=idem,
                require_approval=True,workload_class="media",
            )
        stored=ticket.get("payload",{})
        if stored.get("kind")!="business_opportunity_video_approval" or stored.get("opportunity_id")!=opportunity_id or stored.get("initiative_id")!=initiative["id"] or stored.get("video_brief_sha256")!=payload["video_brief_sha256"]:
            raise ValueError("Commander idempotency key is already bound to a different approval request")
        linked=any(
            json.loads(row["details_json"]).get("ticket_id")==ticket["id"]
            for row in self.connection.execute(
                "SELECT details_json FROM business_opportunity_events WHERE opportunity_id=? AND event_type='video_approval_requested'",
                (opportunity_id,),
            ).fetchall()
        )
        if not linked:
            self._event(opportunity_id,"video_approval_requested",details={"ticket_id":ticket["id"],"initiative_id":initiative["id"],"attempt_id":attempt_id})
            self.connection.commit()
        return {"ticket":ticket,"initiative_id":initiative["id"],"video_attempt_created":False}

    def queue_video_after_approval(self,opportunity_id,approval_task_id,*,initiative_database,task_database):
        """Queue one deterministic GPU attempt only after a persisted matching human approval."""
        from muse.initiative_engine import InitiativeStore
        from muse.task_queue import TaskQueue
        opportunity=self.get(opportunity_id)
        if opportunity["status"] in {"rejected","parked"}:
            raise ValueError("rejected or parked opportunities cannot queue video")
        initiative=self._linked_initiative(opportunity_id,initiative_database=initiative_database)
        if initiative is None:
            raise KeyError("promote the opportunity to a Creator Initiative first")
        if initiative["status"] in {"rejected","parked"}:
            raise ValueError("rejected or parked Creator Initiatives cannot queue video")
        with TaskQueue(task_database) as queue:
            ticket=queue.get(approval_task_id)
            if ticket is None:
                raise KeyError(approval_task_id)
            events=queue.events(approval_task_id,limit=500)
        payload=ticket.get("payload",{})
        approval=payload.get("_approval",{})
        matching=isinstance(approval,dict) and isinstance(approval.get("approved_by"),str) and bool(approval.get("approved_by").strip()) and any(
            event.get("event_type")=="human.decision"
            and event.get("actor")==approval.get("approved_by")
            and isinstance(event.get("payload"),dict)
            and event["payload"].get("decision")=="approved"
            and TaskQueue.approval_timestamp_matches_event(approval.get("approved_at"), event.get("created_at"))
            for event in events
        )
        if (ticket.get("status")!="queued"
            or payload.get("kind")!="business_opportunity_video_approval"
            or payload.get("opportunity_id")!=opportunity_id
            or payload.get("initiative_id")!=initiative["id"]
            or payload.get("approval_scope")!="one_video_attempt_only"
            or payload.get("_approved") is not True
            or not matching):
            raise PermissionError("video generation requires the matching persisted Commander human.decision approval")
        brief=payload.get("video_brief")
        if not isinstance(brief,dict) or not brief.get("title") or not brief.get("prompt"):
            raise ValueError("approved ticket has no valid video brief")
        concept=json.loads(initiative["concept"])
        stored=concept.get("creator_brief",{}).get("video") if isinstance(concept,dict) else None
        digest=lambda value: hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode("utf-8")).hexdigest()
        if not isinstance(stored,dict) or digest(stored)!=payload.get("video_brief_sha256") or digest(stored)!=digest(brief):
            raise ValueError("approved video brief no longer matches the initiative source")
        attempt_id=payload.get("attempt_id")
        with InitiativeStore(initiative_database) as initiatives:
            existing=initiatives.get_video_attempt(attempt_id)
            if existing is not None:
                if existing.get("initiative_id")!=initiative["id"] or existing.get("mode")!="prompt_to_video":
                    raise ValueError("deterministic video attempt ID is already bound to another task")
                if existing.get("approval_task_id")!=approval_task_id:
                    raise ValueError("existing video attempt is not bound to this Commander approval ticket")
                if existing.get("recipe_sha256") != brief.get("recipe_sha256"):
                    raise ValueError("existing video attempt is bound to a different approved recipe fingerprint")
                attempt=existing
                created=False
            else:
                attempt=initiatives.start_video_attempt(
                    attempt_id,mode="prompt_to_video",initiative_id=initiative["id"],
                    approval_task_id=approval_task_id,
                    recipe_sha256=brief.get("recipe_sha256"),
                )
                created=True
        linked=any(
            json.loads(row["details_json"]).get("attempt_id")==attempt_id
            for row in self.connection.execute(
                "SELECT details_json FROM business_opportunity_events WHERE opportunity_id=? AND event_type='video_attempt_queued'",
                (opportunity_id,),
            ).fetchall()
        )
        if not linked:
            self._event(opportunity_id,"video_attempt_queued",details={"attempt_id":attempt_id,"approval_task_id":approval_task_id,"initiative_id":initiative["id"]})
            self.connection.commit()
        return {"attempt":attempt,"created":created,"approval_task_id":approval_task_id,"gpu_execution":"not_started"}

    def close(self): self.connection.close()
    def __enter__(self): return self
    def __exit__(self,*exc): self.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description="Muse local business opportunity ledger")
    parser.add_argument("--db",default="data/business/opportunities.sqlite3")
    sub=parser.add_subparsers(dest="command",required=True)
    add=sub.add_parser("add")
    for name in ("title","customer-segment","customer-problem","proposed-offer","validation-test","success-signal","idempotency-key"):
        add.add_argument("--"+name,required=True)
    add.add_argument("--currency",default="USD")
    add.add_argument("--estimated-price-minor",type=int,default=0); add.add_argument("--estimated-cost-minor",type=int,default=0)
    for name in DIMENSIONS: add.add_argument("--"+name.replace("_","-"),type=int,required=True)
    ev=sub.add_parser("evidence"); ev.add_argument("opportunity_id")
    ev.add_argument("--idempotency-key",required=True); ev.add_argument("--evidence-type",choices=sorted(EVIDENCE_TYPES),required=True)
    ev.add_argument("--epistemic-status",choices=sorted(EPISTEMIC),required=True); ev.add_argument("--summary",required=True); ev.add_argument("--source-url",default="")
    short=sub.add_parser("shortlist"); short.add_argument("--limit",type=int,default=3)
    promote=sub.add_parser("promote"); promote.add_argument("opportunity_id"); promote.add_argument("--initiative-db",default="data/initiatives/muse.sqlite3")
    request=sub.add_parser("request-video-approval"); request.add_argument("opportunity_id"); request.add_argument("--initiative-db",default="data/initiatives/muse.sqlite3"); request.add_argument("--task-db",default="data/tasks/muse.sqlite3")
    queue_video=sub.add_parser("queue-approved-video"); queue_video.add_argument("opportunity_id"); queue_video.add_argument("approval_task_id"); queue_video.add_argument("--initiative-db",default="data/initiatives/muse.sqlite3"); queue_video.add_argument("--task-db",default="data/tasks/muse.sqlite3")
    args=parser.parse_args(argv)
    with BusinessOpportunityStore(args.db) as store:
        if args.command=="add":
            result=store.create(idempotency_key=args.idempotency_key,title=args.title,customer_segment=args.customer_segment,
              customer_problem=args.customer_problem,proposed_offer=args.proposed_offer,validation_test=args.validation_test,
              success_signal=args.success_signal,ratings={n:getattr(args,n) for n in DIMENSIONS},currency=args.currency,
              estimated_price_minor=args.estimated_price_minor,estimated_cost_minor=args.estimated_cost_minor)
        elif args.command=="evidence":
            result=store.add_evidence(args.opportunity_id,idempotency_key=args.idempotency_key,evidence_type=args.evidence_type,
              epistemic_status=args.epistemic_status,summary=args.summary,source_url=args.source_url)
        elif args.command=="promote": result=store.promote_to_initiative(args.opportunity_id,initiative_database=args.initiative_db)
        elif args.command=="request-video-approval": result=store.request_video_approval(args.opportunity_id,initiative_database=args.initiative_db,task_database=args.task_db)
        elif args.command=="queue-approved-video": result=store.queue_video_after_approval(args.opportunity_id,args.approval_task_id,initiative_database=args.initiative_db,task_database=args.task_db)
        else: result=store.shortlist(limit=args.limit)
    print(json.dumps(result,ensure_ascii=False,indent=2)); return 0


if __name__=="__main__": raise SystemExit(main())
