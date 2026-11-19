"""Read-only aggregation and symbolic-hypothesis views for the Muse Cyber-Astrolabe client.

Every quantitative counter in this module reads real local SQLite or filesystem state
with read-only connections. Symbolic systems (Bagua, 28 Star Mansions, Yi Jing,
and cross-tradition lenses) are strictly labeled as non-predictive hypotheses and
never auto-write User Track memories or bypass approvals.
"""
from __future__ import annotations

from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any
from urllib.parse import quote

from muse.capabilities import CapabilityRegistry
from muse.inspiration import (
    CORE_TRADITION_PROFILES,
    SYMBOLIC_LENSES,
    build_cross_system_inspiration,
    build_symbolic_inspiration_pack,
)
from muse.research_knowledge import NODE_TYPES
from muse.research_wiki_audit import audit_database
from muse.resource_governor import ResourcePolicy, probe_host_resources
from muse.sexagenary import (
    REFERENCE_SOURCE_REFS,
    hour_branch_from_clock,
    sexagenary_day,
    sexagenary_hour,
    sexagenary_year,
)
from muse.specialist_router import (
    _contains_term,
    _normalise,
    builtin_specialist_profiles,
)
from muse.task_queue import TaskQueue
from muse.yijing import TRIGRAM_BY_BITS, analyze_yijing_lines, seeded_three_coin_cast


BAGUA_TRIGRAMS: tuple[dict[str, str], ...] = (
    {
        "bits_bottom_to_top": "111",
        "name": "乾",
        "symbol": "☰",
        "nature": "天 · 创生动力",
        "direction": "西北（后天）/ 正南（先天）",
        "element": "金",
        "system_lens": "顶层目标、核心准则与自驱能量源",
        "inquiry_prompt": "当前目标最底层的第一性原理和不可妥协的宪章是什么？",
    },
    {
        "bits_bottom_to_top": "110",
        "name": "兑",
        "symbol": "☱",
        "nature": "泽 · 交互共鸣",
        "direction": "正西",
        "element": "金",
        "system_lens": "界面表达、信息反馈与跨体对话",
        "inquiry_prompt": "用户与外部系统通过什么接口获得清晰、愉悦且诚实的反馈？",
    },
    {
        "bits_bottom_to_top": "101",
        "name": "离",
        "symbol": "☲",
        "nature": "火 · 洞察照明",
        "direction": "正南",
        "element": "火",
        "system_lens": "证据显影、知识图谱可见性与视觉呈现",
        "inquiry_prompt": "哪些关键线索、反证或视觉结构尚未被点亮并呈现出来？",
    },
    {
        "bits_bottom_to_top": "100",
        "name": "震",
        "symbol": "☳",
        "nature": "雷 · 触发启动",
        "direction": "正东",
        "element": "木",
        "system_lens": "事件触发器、点火脉冲与任务入队信号",
        "inquiry_prompt": "是什么真实事件或灵感脉冲触发了这一轮探索与行动？",
    },
    {
        "bits_bottom_to_top": "011",
        "name": "巽",
        "symbol": "☴",
        "nature": "风 · 渗透传导",
        "direction": "东南",
        "element": "木",
        "system_lens": "信息流渗透、跨学科类比迁移与传播",
        "inquiry_prompt": "如果把方法或模式迁移到陌生领域，会渗透出什么新组合？",
    },
    {
        "bits_bottom_to_top": "010",
        "name": "坎",
        "symbol": "☵",
        "nature": "水 · 险阻深流",
        "direction": "正北",
        "element": "水",
        "system_lens": "风险边界、并发背压、未知深水区与容错",
        "inquiry_prompt": "任务在资源、权限或证据上最深的暗礁在哪里，如何安全回滚？",
    },
    {
        "bits_bottom_to_top": "001",
        "name": "艮",
        "symbol": "☶",
        "nature": "山 · 止界门禁",
        "direction": "东北",
        "element": "土",
        "system_lens": "安全闸门、人工审批停顿与独立审查边界",
        "inquiry_prompt": "在什么地方系统必须主动止步，等待证据复核或创造者批准？",
    },
    {
        "bits_bottom_to_top": "000",
        "name": "坤",
        "symbol": "☷",
        "nature": "地 · 承载沉淀",
        "direction": "西南",
        "element": "土",
        "system_lens": "本地持久化、双轨记忆库与经验复用底座",
        "inquiry_prompt": "这一轮成果将如何沉淀到本地知识与经验库，供下一轮继承？",
    },
)


STAR_MANSION_QUADRANTS: dict[str, dict[str, str]] = {
    "azure_dragon": {"name": "东方苍龙", "season": "春 · 启生", "color_token": "cyan"},
    "black_tortoise": {"name": "北方玄武", "season": "冬 · 蓄藏", "color_token": "blue"},
    "white_tiger": {"name": "西方白虎", "season": "秋 · 决断", "color_token": "silver"},
    "vermilion_bird": {"name": "南方朱雀", "season": "夏 · 显化", "color_token": "amber"},
}


STAR_MANSIONS: tuple[dict[str, str], ...] = (
    # 东方苍龙七宿
    {"index": "1", "name": "角", "full_name": "角木蛟", "quadrant": "azure_dragon", "element": "木", "theme": "破土立纲", "inquiry": "这一步探索需要确立什么清晰边界或数据结构？"},
    {"index": "2", "name": "亢", "full_name": "亢金龙", "quadrant": "azure_dragon", "element": "金", "theme": "刚健承压", "inquiry": "过快推进时，系统的刚性约束与停机条件在哪里？"},
    {"index": "3", "name": "氐", "full_name": "氐土貉", "quadrant": "azure_dragon", "element": "土", "theme": "筑基固本", "inquiry": "有哪些底层数据或测试基线需要先扎稳根基？"},
    {"index": "4", "name": "房", "full_name": "房日兔", "quadrant": "azure_dragon", "element": "日", "theme": "中枢明堂", "inquiry": "Commander 调度中枢与各模块之间的权责是否清楚？"},
    {"index": "5", "name": "心", "full_name": "心月狐", "quadrant": "azure_dragon", "element": "月", "theme": "意图内核", "inquiry": "剥离装饰后，用户真正的创作核心意图是什么？"},
    {"index": "6", "name": "尾", "full_name": "尾火虎", "quadrant": "azure_dragon", "element": "火", "theme": "收尾传导", "inquiry": "本阶段如何收敛出可验证的作品或经验闭环？"},
    {"index": "7", "name": "箕", "full_name": "箕水豹", "quadrant": "azure_dragon", "element": "水", "theme": "筛沙扬清", "inquiry": "哪些噪声与过时假设应当从候选知识中筛除？"},
    # 北方玄武七宿
    {"index": "8", "name": "斗", "full_name": "斗木獬", "quadrant": "black_tortoise", "element": "木", "theme": "度量规划", "inquiry": "工作量估算、容量窗口与优先级排序是否合理？"},
    {"index": "9", "name": "牛", "full_name": "牛金牛", "quadrant": "black_tortoise", "element": "金", "theme": "稳健耕耘", "inquiry": "长期后台维护与常规任务怎样做到低耗稳定？"},
    {"index": "10", "name": "女", "full_name": "女土蝠", "quadrant": "black_tortoise", "element": "土", "theme": "织网连结", "inquiry": "分散的碎片笔记之间存在什么隐藏的结构同构？"},
    {"index": "11", "name": "虚", "full_name": "虚日鼠", "quadrant": "black_tortoise", "element": "日", "theme": "留白存疑", "inquiry": "当前哪些结论证据尚虚，必须明确标注为未知？"},
    {"index": "12", "name": "危", "full_name": "危月燕", "quadrant": "black_tortoise", "element": "月", "theme": "高处警觉", "inquiry": "是否设置了看门狗、租约超时与异常崩溃恢复？"},
    {"index": "13", "name": "室", "full_name": "室火猪", "quadrant": "black_tortoise", "element": "火", "theme": "营室筑库", "inquiry": "本机存储、知识账本与资产路径是否隔离有序？"},
    {"index": "14", "name": "壁", "full_name": "壁水貐", "quadrant": "black_tortoise", "element": "水", "theme": "典籍壁藏", "inquiry": "如何确保关键来源、哈希与许可证完整归档不失真？"},
    # 西方白虎七宿
    {"index": "15", "name": "奎", "full_name": "奎木狼", "quadrant": "white_tiger", "element": "木", "theme": "文脉索引", "inquiry": "研究资料的引用索引与作者谱系是否清晰可追溯？"},
    {"index": "16", "name": "娄", "full_name": "娄金狗", "quadrant": "white_tiger", "element": "金", "theme": "聚拢协同", "inquiry": "多智能体角色之间需要怎样交接中间产出？"},
    {"index": "17", "name": "胃", "full_name": "胃土雉", "quadrant": "white_tiger", "element": "土", "theme": "消化吸收", "inquiry": "外部搜索信息是否经过反证与提纯才进入知识库？"},
    {"index": "18", "name": "昴", "full_name": "昴日鸡", "quadrant": "white_tiger", "element": "日", "theme": "晨钟自检", "inquiry": "定时自检与异常告警能否在静默故障前发出提示？"},
    {"index": "19", "name": "毕", "full_name": "毕月乌", "quadrant": "white_tiger", "element": "月", "theme": "网罗反证", "inquiry": "有没有主动搜集与主流假设相反的反面证据与替代解释？"},
    {"index": "20", "name": "觜", "full_name": "觜火猴", "quadrant": "white_tiger", "element": "火", "theme": "慎言审辩", "inquiry": "输出结论中哪些只是启发类比，哪些才经得起审辩？"},
    {"index": "21", "name": "参", "full_name": "参水猿", "quadrant": "white_tiger", "element": "水", "theme": "参验对比", "inquiry": "不同方案能否在隔离沙盒中跑同一基线对照实验？"},
    # 南方朱雀七宿
    {"index": "22", "name": "井", "full_name": "井木犴", "quadrant": "vermilion_bird", "element": "木", "theme": "秩序法度", "inquiry": "并发资源限制与权限边界是否做到井然有序？"},
    {"index": "23", "name": "鬼", "full_name": "鬼金羊", "quadrant": "vermilion_bird", "element": "金", "theme": "察微鉴幽", "inquiry": "隐藏在日志或失败码里的细微偏差是否被识别？"},
    {"index": "24", "name": "柳", "full_name": "柳土獐", "quadrant": "vermilion_bird", "element": "土", "theme": "柔韧适配", "inquiry": "当某项外部工具离线时，系统是否有优雅降级路线？"},
    {"index": "25", "name": "星", "full_name": "星日马", "quadrant": "vermilion_bird", "element": "日", "theme": "点亮火种", "inquiry": "今天最值得投入精力的单点创造火花是什么？"},
    {"index": "26", "name": "张", "full_name": "张月鹿", "quadrant": "vermilion_bird", "element": "月", "theme": "张弓蓄势", "inquiry": "素材、脚本、模型与审片资源是否已齐备待发？"},
    {"index": "27", "name": "翼", "full_name": "翼火蛇", "quadrant": "vermilion_bird", "element": "火", "theme": "振翼成片", "inquiry": "怎样把抽象研究编译为可体验的影像、软件或工具？"},
    {"index": "28", "name": "轸", "full_name": "轸水蚓", "quadrant": "vermilion_bird", "element": "水", "theme": "载道远行", "inquiry": "打包好的作品如何经过人工验收再稳健传承给下一代？"},
)


def _ro_connect(path: str | Path | None) -> sqlite3.Connection | None:
    """Open an existing non-symlink SQLite database in strict read-only mode."""
    if path is None:
        return None
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        return None
    resolved = target.resolve()
    uri = "file:" + quote(resolved.as_posix(), safe="/:") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _safe_count(connection: sqlite3.Connection | None, sql: str, params: tuple[Any, ...] = ()) -> int:
    if connection is None:
        return 0
    try:
        row = connection.execute(sql, params).fetchone()
        return int(row[0]) if row is not None else 0
    except sqlite3.Error:
        return 0


def _safe_rows(connection: sqlite3.Connection | None, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    if connection is None:
        return []
    try:
        return list(connection.execute(sql, params).fetchall())
    except sqlite3.Error:
        return []


def _resolve_data_root_from_task_db(task_db: str | Path) -> Path:
    resolved = Path(task_db).resolve()
    if resolved.parent.name == "tasks":
        return resolved.parent.parent
    if (resolved.parent / "data").is_dir():
        return resolved.parent / "data"
    return resolved.parent


def _count_products_dir(products_dir: str | Path | None) -> int:
    if products_dir is None:
        return 0
    root = Path(products_dir)
    if root.is_symlink() or not root.is_dir():
        return 0
    count = 0
    try:
        for initiative_root in root.iterdir():
            if (
                initiative_root.is_symlink()
                or not initiative_root.is_dir()
                or not re.fullmatch(r"[0-9a-f]{32}", initiative_root.name)
            ):
                continue
            for version_dir in initiative_root.iterdir():
                if (
                    not version_dir.is_symlink()
                    and version_dir.is_dir()
                    and re.fullmatch(r"[0-9a-f]{20}", version_dir.name)
                    and (version_dir / "manifest.json").is_file()
                ):
                    count += 1
    except OSError:
        return count
    return count


def _count_report_artifacts(report_dir: str | Path | None) -> int:
    if report_dir is None:
        return 0
    root = Path(report_dir)
    if root.is_symlink() or not root.is_dir():
        return 0
    try:
        return sum(
            1
            for item in root.iterdir()
            if not item.is_symlink() and item.is_file() and item.suffix.lower() == ".md"
        )
    except OSError:
        return 0


def build_capability_matrix(
    *,
    knowledge_configured: bool,
    agent_count: int,
    orchestration_runs: int,
    video_attempts: int,
    products_count: int,
) -> list[dict[str, Any]]:
    """Return an honest inventory of currently implemented capabilities vs long-term vision."""
    return [
        {
            "id": "commander_queue",
            "title": "唯一 Muse Commander & 持久任务队列",
            "pillar": "唯一 Commander",
            "status": "implemented",
            "status_label": "已实现 · 本地验证",
            "summary": "SQLite 持久化任务队列、租约/心跳、崩溃回收、依赖 DAG 规划与独立审查门禁。",
            "evidence": f"已观测 {orchestration_runs} 次历史编排记录；桌面当前提供只读队列与工作流监控。",
            "entry": "#queue · #agents",
            "boundary": "桌面尚不开放直接批准/取消/重启队列写操作，保留 CLI 显式审批保护。",
        },
        {
            "id": "research_wiki_starmap",
            "title": "Research Wiki 知识星图 & 证据治理",
            "pillar": "求知与传承",
            "status": "implemented",
            "status_label": "已实现 · 持久图谱" if knowledge_configured else "就绪 · 待初始化",
            "summary": "支持 source/idea/experiment/claim/decision/failure 节点、关系边、证据候选与只读审计。",
            "evidence": "桌面知识星图实时浏览真实子图与治理指标，支持节点邻域跃迁。",
            "entry": "#knowledge-starmap",
            "boundary": "候选证据尚需人工审查；不会自动把未审查主张晋升为已验证事实。",
        },
        {
            "id": "metaphysics_astrolabe",
            "title": "星环 / 八卦 / 二十八星宿 & 象征灵感推演",
            "pillar": "灵感与玄学根基",
            "status": "implemented",
            "status_label": "已实现 · 假设引擎",
            "summary": "确定性干支历算、易经三钱法/六爻/文王 64 卦及互错综对照、四体系研究提问矩阵。",
            "evidence": "全量输出带 symbolic_hypothesis_only 与 is_evidence=false 标签。",
            "entry": "#overview · #celestial-lab",
            "boundary": "可选外部排盘库（奇门/紫微/六壬）按本机安装探测状态显式报告；绝不作事实预测。",
        },
        {
            "id": "dual_track_memory",
            "title": "User / Agent 双轨长期记忆 & Obsidian 只读镜像",
            "pillar": "文明记忆",
            "status": "backend_only",
            "status_label": "后端/CLI 已实现",
            "summary": "支持分类记忆、修订历史、软删除、双轨严格隔离及 Obsidian Markdown 单向导出审计。",
            "evidence": "桌面总览仅统计双轨数量以保护隐私，不直接暴露或改写 User Track 原文。",
            "entry": "CLI: muse.memory_cli",
            "boundary": "桌面端暂未开放记忆编辑/检索管理页，也未开启 Obsidian 双向自动回写。",
        },
        {
            "id": "autonomous_daemon",
            "title": "24h 自主监督运行时 & 一键创作流水线",
            "pillar": "自主规划与自检",
            "status": "implemented",
            "status_label": "已实现 · 受控启停",
            "summary": "本地常驻守护进程启停/暂停/恢复/单轮运行，支持可复现研究型短片配方提交与进度追踪。",
            "evidence": "支持 SQLite 单例租约与 control-log.jsonl 审计记录。",
            "entry": "#daemon-runtime · #pipeline",
            "boundary": "桌面点击后才按需运行；不会在未授权时开机自启或安装 Windows 系统服务。",
        },
        {
            "id": "value_video_factory",
            "title": "灵感进化工坊 & Remotion/Wan2.1 双画幅成片引擎",
            "pillar": "创造工坊",
            "status": "implemented",
            "status_label": "已实现 · 双画幅成片",
            "summary": "六维选题启发评分、灵感→正反深研→6幕高留存分镜编译、Remotion 1080P (9:16/16:9) 原生动画、中/粤语旁白与 YouTube/抖音/视频号发布包。",
            "evidence": f"已登记 {products_count} 项交付产物、{video_attempts} 次视频尝试记录。",
            "entry": "#value-studio · #pipeline · #products",
            "boundary": "AI 镜头依赖本机 ComfyUI/Wan；所有成片保持 draft_only/testing，人工审片核对后再手动上传各平台。",
        },
        {
            "id": "agent_galaxy_dispatch",
            "title": "AI Agent 星系拓扑 & 多供应商实时接入",
            "pillar": "多 Agent 协作",
            "status": "partial",
            "status_label": "部分实现 · 模板就绪" if agent_count == 0 else f"已接入 {agent_count} 个 Agent",
            "summary": "内置 8 专家角色模型与 7 项角色准入策略，支持路由关键词/能力匹配预演与真实效果指标追踪。",
            "evidence": f"当前实连 Agent 数：{agent_count}；未连接外部进程不会伪装成在线节点。",
            "entry": "#agents",
            "boundary": "外部供应商与真实 Ruflo/HTTP Agent 需单独注册在线后方可实际派发。",
        },
        {
            "id": "self_evolution_radar",
            "title": "自主灵感跟进、能力雷达 & 可回滚自我改进闭环",
            "pillar": "自我进化",
            "status": "implemented",
            "status_label": "已实现 · 审批门禁",
            "summary": "Creator Inbox 灵感自动/一键触发双路深研与反证检验、自动生成结构化改进提案与升级档案（ready_for_review）、能力雷达跨窗口趋势追踪。",
            "evidence": "提案附带验收标准、回滚方案与来源哈希；提案者不能批准自己。",
            "entry": "#inspirations · #feedback",
            "boundary": "桌面不开放自动修改源码或一键批准按钮；代码级进化必须经独立 Reviewer 与人工批准。",
        },
        {
            "id": "prometheus_horizon_vision",
            "title": "全宇宙跨媒介无限工坊 & 系统级常驻服务",
            "pillar": "PROMETHEUS 长期愿景",
            "status": "vision",
            "status_label": "长期愿景 · 规划中",
            "summary": "从任意灵感自主编译软件、3D/游戏/音频/交互作品，多机安全联邦，以及系统托盘与完整设置中心。",
            "evidence": "记录于 docs/VISION.md 与 docs/PROJECT_PROMETHEUS.md 长期宪章。",
            "entry": "#prometheus-core",
            "boundary": "尚未实现全自动无人工验收的产品发布或跨平台商业变现闭环。",
        },
    ]


def build_prometheus_core(
    *,
    task_db: str | Path,
    progress_db: str | Path,
    capability_db: str | Path,
    improvement_db: str | Path | None = None,
    knowledge_db: str | Path | None = None,
    report_dir: str | Path | None = None,
    products_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Aggregate Prometheus five-stage counters, philosophy principles, and host resource limits."""
    data_root = _resolve_data_root_from_task_db(task_db)

    knowledge_conn = _ro_connect(knowledge_db)
    initiatives_conn = _ro_connect(data_root / "initiatives" / "muse.sqlite3")
    reminders_conn = _ro_connect(data_root / "reminders" / "muse.sqlite3")
    evidence_conn = _ro_connect(data_root / "research" / "evidence.sqlite3")
    websites_conn = _ro_connect(data_root / "research" / "websites.sqlite3")
    philosophy_conn = _ro_connect(data_root / "philosophy" / "muse.sqlite3")
    memory_conn = _ro_connect(data_root / "memory" / "muse.sqlite3")
    task_conn = _ro_connect(task_db)
    progress_conn = _ro_connect(progress_db)
    improvement_conn = _ro_connect(improvement_db)
    agent_conn = _ro_connect(data_root / "agents" / "muse.sqlite3")

    try:
        # Stage 1: Ignition
        inspirations_count = 0
        nodes_by_type: dict[str, int] = {t: 0 for t in sorted(NODE_TYPES)}
        edges_by_type: dict[str, int] = {}
        knowledge_nodes_total = 0
        knowledge_edges_total = 0
        claim_evidence_count = 0
        if knowledge_conn is not None:
            for row in _safe_rows(
                knowledge_conn,
                "SELECT node_type, COUNT(*) AS cnt FROM knowledge_nodes WHERE deleted_at IS NULL GROUP BY node_type",
            ):
                nodes_by_type[str(row["node_type"])] = int(row["cnt"])
            knowledge_nodes_total = sum(nodes_by_type.values())
            for row in _safe_rows(
                knowledge_conn,
                "SELECT e.edge_type, COUNT(*) AS cnt FROM knowledge_edges e "
                "JOIN knowledge_nodes s ON s.id=e.source_id "
                "JOIN knowledge_nodes t ON t.id=e.target_id "
                "WHERE s.deleted_at IS NULL AND t.deleted_at IS NULL GROUP BY e.edge_type",
            ):
                edges_by_type[str(row["edge_type"])] = int(row["cnt"])
            knowledge_edges_total = sum(edges_by_type.values())
            claim_evidence_count = _safe_count(knowledge_conn, "SELECT COUNT(*) FROM claim_evidence")
            for row in _safe_rows(
                knowledge_conn,
                "SELECT metadata_json FROM knowledge_nodes WHERE deleted_at IS NULL AND node_type='claim'",
            ):
                try:
                    meta = json.loads(row["metadata_json"])
                except (TypeError, json.JSONDecodeError):
                    meta = {}
                if isinstance(meta, dict) and meta.get("origin") == "creator_inspiration_inbox":
                    inspirations_count += 1

        initiatives_count = _safe_count(initiatives_conn, "SELECT COUNT(*) FROM creator_initiatives")
        reminders_count = _safe_count(reminders_conn, "SELECT COUNT(*) FROM reminders")

        # Stage 2: Discovery
        research_evidence_count = _safe_count(evidence_conn, "SELECT COUNT(*) FROM research_evidence")
        research_sites_count = _safe_count(websites_conn, "SELECT COUNT(*) FROM research_sites")

        # Stage 3: Synthesis
        principles: list[dict[str, Any]] = []
        for row in _safe_rows(
            philosophy_conn,
            "SELECT p.principle_key, p.title, p.description, p.practice, p.confidence, "
            "COUNT(DISTINCT l.experience_id) AS experience_count, "
            "COUNT(DISTINCT e.id) AS experiment_count "
            "FROM philosophy_principles p "
            "LEFT JOIN philosophy_links l ON l.principle_id=p.id "
            "LEFT JOIN philosophy_experiments e ON e.principle_id=p.id "
            "WHERE p.status='active' "
            "GROUP BY p.id ORDER BY p.confidence DESC, experience_count DESC, p.title LIMIT 12",
        ):
            principles.append(
                {
                    "principle_key": str(row["principle_key"]),
                    "title": str(row["title"]),
                    "description": str(row["description"]),
                    "practice": str(row["practice"]),
                    "confidence": float(row["confidence"]),
                    "experience_count": int(row["experience_count"]),
                    "experiment_count": int(row["experiment_count"]),
                }
            )
        philosophy_principles_count = _safe_count(
            philosophy_conn, "SELECT COUNT(*) FROM philosophy_principles WHERE status='active'"
        )
        philosophy_experiences_count = _safe_count(philosophy_conn, "SELECT COUNT(*) FROM philosophy_experiences")
        philosophy_links_count = _safe_count(philosophy_conn, "SELECT COUNT(*) FROM philosophy_links")
        philosophy_proposals_count = _safe_count(
            philosophy_conn, "SELECT COUNT(*) FROM philosophy_principle_proposals"
        )

        # Stage 4: Creation
        products_count = _count_products_dir(products_dir)
        artifacts_count = _count_report_artifacts(report_dir)
        orchestration_runs_count = _safe_count(task_conn, "SELECT COUNT(*) FROM orchestration_runs")
        agent_runs_count = _safe_count(task_conn, "SELECT COUNT(*) FROM agent_runs")
        video_attempts_total = _safe_count(initiatives_conn, "SELECT COUNT(*) FROM initiative_video_attempts")
        video_attempts_succeeded = _safe_count(
            initiatives_conn, "SELECT COUNT(*) FROM initiative_video_attempts WHERE status='succeeded'"
        )

        # Stage 5: Transmission (counts only for memory tracks; never leak private content)
        user_track_memories_count = 0
        agent_track_memories_count = 0
        for row in _safe_rows(
            memory_conn,
            "SELECT track, COUNT(*) AS cnt FROM memories WHERE deleted_at IS NULL GROUP BY track",
        ):
            if row["track"] == "user":
                user_track_memories_count = int(row["cnt"])
            elif row["track"] == "agent":
                agent_track_memories_count = int(row["cnt"])

        progress_events_count = _safe_count(progress_conn, "SELECT COUNT(*) FROM progress_events")
        watchdog_checks_count = _safe_count(progress_conn, "SELECT COUNT(*) FROM watchdog_checks")
        improvement_proposals_count = _safe_count(improvement_conn, "SELECT COUNT(*) FROM improvement_proposals")
        agent_count = _safe_count(agent_conn, "SELECT COUNT(*) FROM agents")

        capabilities_count = 0
        cap_target = Path(capability_db)
        if not cap_target.is_symlink() and cap_target.is_file():
            with CapabilityRegistry(cap_target) as cap_reg:
                capabilities_count = len(cap_reg.list_all())

        resource_decision = ResourcePolicy().decide(probe_host_resources()).as_dict()
        stages = {
            "ignition": {
                "label": "点火 · Ignition",
                "inspirations_count": inspirations_count,
                "initiatives_count": initiatives_count,
                "reminders_count": reminders_count,
            },
            "discovery": {
                "label": "求知 · Discovery",
                "knowledge_nodes_total": knowledge_nodes_total,
                "nodes_by_type": nodes_by_type,
                "claim_evidence_count": claim_evidence_count,
                "research_evidence_count": research_evidence_count,
                "research_sites_count": research_sites_count,
            },
            "synthesis": {
                "label": "融合 · Synthesis",
                "knowledge_edges_total": knowledge_edges_total,
                "edges_by_type": edges_by_type,
                "philosophy_principles_count": philosophy_principles_count,
                "philosophy_experiences_count": philosophy_experiences_count,
                "philosophy_links_count": philosophy_links_count,
                "philosophy_proposals_count": philosophy_proposals_count,
            },
            "creation": {
                "label": "创造 · Creation",
                "products_count": products_count,
                "artifacts_count": artifacts_count,
                "orchestration_runs_count": orchestration_runs_count,
                "agent_runs_count": agent_runs_count,
                "video_attempts_total": video_attempts_total,
                "video_attempts_succeeded": video_attempts_succeeded,
            },
            "transmission": {
                "label": "传承 · Transmission",
                "user_track_memories_count": user_track_memories_count,
                "agent_track_memories_count": agent_track_memories_count,
                "progress_events_count": progress_events_count,
                "watchdog_checks_count": watchdog_checks_count,
                "capabilities_count": capabilities_count,
                "improvement_proposals_count": improvement_proposals_count,
            },
        }
        return {
            "mission": "让知识之火不断传递，让未知成为探索的起点。",
            "stages": stages,
            "principles": principles,
            "resource_governor": resource_decision,
            "capability_matrix": build_capability_matrix(
                knowledge_configured=knowledge_conn is not None,
                agent_count=agent_count,
                orchestration_runs=orchestration_runs_count,
                video_attempts=video_attempts_total,
                products_count=products_count,
            ),
            "memory_privacy_guard": {
                "user_track_text_exposed": False,
                "auto_writeback_enabled": False,
            },
        }
    finally:
        for conn in (
            knowledge_conn,
            initiatives_conn,
            reminders_conn,
            evidence_conn,
            websites_conn,
            philosophy_conn,
            memory_conn,
            task_conn,
            progress_conn,
            improvement_conn,
            agent_conn,
        ):
            if conn is not None:
                conn.close()


def build_knowledge_graph(
    knowledge_db: str | Path | None,
    *,
    limit: int = 160,
    node_type: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    """Return a bounded read-only Research Wiki subgraph for star-map visualization."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 250:
        raise ValueError("limit must be between 1 and 250")
    if node_type is not None:
        if not isinstance(node_type, str) or node_type not in NODE_TYPES:
            raise ValueError("invalid knowledge node_type")
    if query is not None:
        if not isinstance(query, str) or len(query) > 120:
            raise ValueError("query must be at most 120 characters")
        query = query.strip()

    conn = _ro_connect(knowledge_db)
    if conn is None:
        return {
            "configured": False,
            "totals": {
                "active_nodes": 0,
                "active_edges": 0,
                "by_type": {t: 0 for t in sorted(NODE_TYPES)},
                "by_status": {},
                "by_edge_type": {},
                "claim_evidence_total": 0,
            },
            "governance": None,
            "nodes": [],
            "edges": [],
        }

    try:
        by_type = {t: 0 for t in sorted(NODE_TYPES)}
        for row in _safe_rows(
            conn,
            "SELECT node_type, COUNT(*) AS cnt FROM knowledge_nodes WHERE deleted_at IS NULL GROUP BY node_type",
        ):
            by_type[str(row["node_type"])] = int(row["cnt"])
        by_status = {
            str(row["status"]): int(row["cnt"])
            for row in _safe_rows(
                conn,
                "SELECT status, COUNT(*) AS cnt FROM knowledge_nodes WHERE deleted_at IS NULL GROUP BY status",
            )
        }
        by_edge_type = {
            str(row["edge_type"]): int(row["cnt"])
            for row in _safe_rows(
                conn,
                "SELECT e.edge_type, COUNT(*) AS cnt FROM knowledge_edges e "
                "JOIN knowledge_nodes s ON s.id=e.source_id "
                "JOIN knowledge_nodes t ON t.id=e.target_id "
                "WHERE s.deleted_at IS NULL AND t.deleted_at IS NULL GROUP BY e.edge_type",
            )
        }
        active_nodes_total = sum(by_type.values())
        active_edges_total = sum(by_edge_type.values())
        claim_evidence_total = _safe_count(conn, "SELECT COUNT(*) FROM claim_evidence")

        # Compute node degrees across active edges only.
        degree_map: dict[str, int] = {}
        for row in _safe_rows(
            conn,
            "SELECT e.source_id, e.target_id FROM knowledge_edges e "
            "JOIN knowledge_nodes s ON s.id=e.source_id "
            "JOIN knowledge_nodes t ON t.id=e.target_id "
            "WHERE s.deleted_at IS NULL AND t.deleted_at IS NULL",
        ):
            s_id = str(row["source_id"])
            t_id = str(row["target_id"])
            degree_map[s_id] = degree_map.get(s_id, 0) + 1
            degree_map[t_id] = degree_map.get(t_id, 0) + 1

        clauses = ["deleted_at IS NULL"]
        params: list[Any] = []
        if node_type is not None:
            clauses.append("node_type = ?")
            params.append(node_type)
        where_sql = " AND ".join(clauses)
        raw_nodes = _safe_rows(
            conn,
            f"SELECT id, node_type, title, summary, status, metadata_json, updated_at FROM knowledge_nodes WHERE {where_sql}",
            tuple(params),
        )
        needle = query.casefold() if query else ""
        filtered: list[tuple[int, str, str, dict[str, Any]]] = []
        for row in raw_nodes:
            title = str(row["title"] or "")
            summary = str(row["summary"] or "")
            if needle and needle not in title.casefold() and needle not in summary.casefold():
                continue
            node_id = str(row["id"])
            degree = degree_map.get(node_id, 0)
            try:
                meta = json.loads(row["metadata_json"])
            except (TypeError, json.JSONDecodeError):
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            updated_at = str(row["updated_at"] or "")
            filtered.append(
                (
                    degree,
                    updated_at,
                    node_id,
                    {
                        "id": node_id,
                        "node_type": str(row["node_type"]),
                        "title": title[:140],
                        "summary": summary[:360],
                        "status": str(row["status"]),
                        "updated_at": updated_at,
                        "degree": degree,
                        "epistemic_status": str(meta.get("epistemic_status", ""))[:80] if isinstance(meta.get("epistemic_status"), str) else "",
                        "review_state": str(meta.get("review_state", ""))[:80] if isinstance(meta.get("review_state"), str) else "",
                        "origin": str(meta.get("origin", ""))[:80] if isinstance(meta.get("origin"), str) else "",
                    },
                )
            )
        filtered.sort(key=lambda item: item[2])
        filtered.sort(key=lambda item: item[1], reverse=True)
        filtered.sort(key=lambda item: item[0], reverse=True)
        selected_nodes = [item[3] for item in filtered[:limit]]
        selected_ids = {item["id"] for item in selected_nodes}

        edges: list[dict[str, Any]] = []
        if selected_ids:
            for row in _safe_rows(
                conn,
                "SELECT e.id, e.source_id, e.target_id, e.edge_type "
                "FROM knowledge_edges e "
                "JOIN knowledge_nodes s ON s.id=e.source_id "
                "JOIN knowledge_nodes t ON t.id=e.target_id "
                "WHERE s.deleted_at IS NULL AND t.deleted_at IS NULL "
                "ORDER BY e.created_at DESC LIMIT 2000",
            ):
                if row["source_id"] in selected_ids and row["target_id"] in selected_ids:
                    edges.append(
                        {
                            "id": str(row["id"]),
                            "source_id": str(row["source_id"]),
                            "target_id": str(row["target_id"]),
                            "edge_type": str(row["edge_type"]),
                        }
                    )
                    if len(edges) >= 600:
                        break
    finally:
        conn.close()

    governance = None
    try:
        governance = audit_database(knowledge_db, limit=5)["summary"]
    except Exception:
        governance = None

    return {
        "configured": True,
        "totals": {
            "active_nodes": active_nodes_total,
            "active_edges": active_edges_total,
            "by_type": by_type,
            "by_status": by_status,
            "by_edge_type": by_edge_type,
            "claim_evidence_total": claim_evidence_total,
        },
        "governance": governance,
        "nodes": selected_nodes,
        "edges": edges,
    }


def build_knowledge_node_enriched(knowledge_db: str | Path, node_id: str) -> dict[str, Any] | None:
    """Return bounded node detail along with direct graph neighbors and evidence summary (read-only)."""
    if not isinstance(node_id, str) or not re.fullmatch(r"[0-9a-f]{32}", node_id):
        raise ValueError("invalid knowledge node id")
    conn = _ro_connect(knowledge_db)
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT * FROM knowledge_nodes WHERE id=? AND deleted_at IS NULL",
            (node_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            metadata = json.loads(row["metadata_json"])
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        try:
            provenance = json.loads(row["provenance_json"])
        except (TypeError, json.JSONDecodeError):
            provenance = []
        if not isinstance(provenance, list):
            provenance = []
        metadata_fields = (
            "origin",
            "epistemic_status",
            "review_state",
            "decision_kind",
            "independent_review_state",
            "improvement_proposal_id",
            "source_feedback_id",
            "evolution_cycle_id",
            "review_id",
            "reviewer_id",
            "approved",
            "creator_initiative_id",
            "candidate_fingerprint",
        )
        safe_metadata = {
            key: metadata[key]
            for key in metadata_fields
            if key in metadata and isinstance(metadata[key], (str, int, float, bool, type(None)))
        }
        node = {
            "id": str(row["id"]),
            "node_type": str(row["node_type"]),
            "title": str(row["title"]),
            "summary": str(row["summary"] or "")[:2000],
            "status": str(row["status"]),
            "provenance": [str(item)[:300] for item in provenance[:30]],
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "metadata": safe_metadata,
        }

        neighbors: list[dict[str, Any]] = []
        out_rows = _safe_rows(
            conn,
            "SELECT e.edge_type, n.id, n.node_type, n.title, n.status "
            "FROM knowledge_edges e "
            "JOIN knowledge_nodes n ON n.id=e.target_id "
            "WHERE e.source_id=? AND n.deleted_at IS NULL "
            "ORDER BY e.created_at DESC LIMIT 12",
            (node_id,),
        )
        for item in out_rows:
            neighbors.append(
                {
                    "id": str(item["id"]),
                    "node_type": str(item["node_type"]),
                    "title": str(item["title"])[:120],
                    "status": str(item["status"]),
                    "direction": "outgoing",
                    "edge_type": str(item["edge_type"]),
                }
            )
        in_rows = _safe_rows(
            conn,
            "SELECT e.edge_type, n.id, n.node_type, n.title, n.status "
            "FROM knowledge_edges e "
            "JOIN knowledge_nodes n ON n.id=e.source_id "
            "WHERE e.target_id=? AND n.deleted_at IS NULL "
            "ORDER BY e.created_at DESC LIMIT 12",
            (node_id,),
        )
        for item in in_rows:
            neighbors.append(
                {
                    "id": str(item["id"]),
                    "node_type": str(item["node_type"]),
                    "title": str(item["title"])[:120],
                    "status": str(item["status"]),
                    "direction": "incoming",
                    "edge_type": str(item["edge_type"]),
                }
            )

        by_direction: dict[str, int] = {}
        for item in _safe_rows(
            conn,
            "SELECT direction, COUNT(*) AS cnt FROM claim_evidence WHERE claim_id=? GROUP BY direction",
            (node_id,),
        ):
            by_direction[str(item["direction"])] = int(item["cnt"])
        evidence_total = sum(by_direction.values())
        reviewed_count = _safe_count(
            conn,
            "SELECT COUNT(DISTINCT r.claim_evidence_id) FROM claim_evidence_reviews r "
            "JOIN claim_evidence e ON e.id=r.claim_evidence_id WHERE e.claim_id=?",
            (node_id,),
        )
        return {
            "node": node,
            "neighbors": neighbors[:24],
            "evidence_summary": {
                "total": evidence_total,
                "by_direction": by_direction,
                "reviewed_count": reviewed_count,
            },
        }
    finally:
        conn.close()


def _read_live_agents(agent_db: str | Path | None) -> tuple[bool, list[dict[str, Any]]]:
    if agent_db is None:
        return False, []
    target = Path(agent_db)
    if target.is_symlink() or not target.is_file():
        return False, []
    from muse.agent_registry import AgentRegistry

    with AgentRegistry(target) as registry:
        agents = registry.list_agents(limit=100)
        return True, [
            {
                "agent_id": agent["agent_id"],
                "role": agent["role"],
                "display_name": agent["display_name"],
                "capabilities": agent["capabilities"],
                "source": agent["source"],
                "enabled": agent["enabled"],
                "status": agent["status"],
                "last_seen_at": agent["last_seen_at"],
                "stale_after_seconds": agent["stale_after_seconds"],
                "current_task_id": agent["current_task_id"],
                "effectiveness": registry.effectiveness(str(agent["agent_id"])),
                "layer": "live_registered_agent",
            }
            for agent in agents
        ]


def build_agent_galaxy(
    task_db: str | Path,
    agent_db: str | Path | None = None,
    capability_db: str | Path | None = None,
) -> dict[str, Any]:
    """Build unified Commander + Specialist Templates + Role Policies + Live Agents galaxy view."""
    queue_counts: dict[str, int] = {
        "queued": 0,
        "running": 0,
        "waiting_approval": 0,
        "succeeded": 0,
        "failed": 0,
        "cancelled": 0,
    }
    task_path = Path(task_db)
    if not task_path.is_symlink() and task_path.is_file():
        with TaskQueue(task_path) as queue:
            queue_counts = queue.snapshot(limit=20)["counts"]

    registry_configured, live_agents = _read_live_agents(agent_db)
    live_by_id = {str(agent["agent_id"]): agent for agent in live_agents}

    task_conn = _ro_connect(task_db)
    role_policies: list[dict[str, Any]] = []
    runs_by_role: dict[str, dict[str, int]] = {}
    orchestration_total = 0
    orchestration_by_status: dict[str, int] = {}
    recent_runs: list[dict[str, Any]] = []
    try:
        for row in _safe_rows(
            task_conn,
            "SELECT role, allowed_capabilities_json, trust_tier, description, updated_at "
            "FROM agent_role_policies ORDER BY role",
        ):
            try:
                allowed_caps = json.loads(row["allowed_capabilities_json"])
            except (TypeError, json.JSONDecodeError):
                allowed_caps = []
            role_policies.append(
                {
                    "role": str(row["role"]),
                    "allowed_capabilities": list(allowed_caps) if isinstance(allowed_caps, list) else [],
                    "trust_tier": str(row["trust_tier"]),
                    "description": str(row["description"] or "")[:240],
                    "updated_at": str(row["updated_at"]),
                }
            )

        for row in _safe_rows(
            task_conn,
            "SELECT role, status, COUNT(*) AS cnt FROM agent_runs GROUP BY role, status",
        ):
            role = str(row["role"])
            bucket = runs_by_role.setdefault(role, {"runs_total": 0, "runs_succeeded": 0, "runs_failed": 0})
            cnt = int(row["cnt"])
            bucket["runs_total"] += cnt
            if row["status"] == "succeeded":
                bucket["runs_succeeded"] += cnt
            elif row["status"] == "failed":
                bucket["runs_failed"] += cnt

        for row in _safe_rows(
            task_conn,
            "SELECT status, COUNT(*) AS cnt FROM orchestration_runs GROUP BY status",
        ):
            orchestration_by_status[str(row["status"])] = int(row["cnt"])
        orchestration_total = sum(orchestration_by_status.values())

        # Strictly redacted recent orchestration summaries: never leak prompt/output/error text.
        for row in _safe_rows(
            task_conn,
            "SELECT r.run_id, r.status, r.created_at, r.updated_at, "
            "COUNT(a.agent_id) AS agent_count, "
            "GROUP_CONCAT(DISTINCT a.role) AS roles "
            "FROM orchestration_runs r "
            "LEFT JOIN agent_runs a ON a.run_id=r.run_id "
            "GROUP BY r.run_id ORDER BY r.updated_at DESC LIMIT 15",
        ):
            roles_raw = row["roles"]
            roles_list = sorted({item for item in str(roles_raw or "").split(",") if item})
            recent_runs.append(
                {
                    "run_id": str(row["run_id"]),
                    "status": str(row["status"]),
                    "created_at": str(row["created_at"]),
                    "updated_at": str(row["updated_at"]),
                    "agent_count": int(row["agent_count"] or 0),
                    "roles": roles_list,
                }
            )
    finally:
        if task_conn is not None:
            task_conn.close()

    policy_by_role = {item["role"]: item for item in role_policies}
    specialist_profiles: list[dict[str, Any]] = []
    for profile in builtin_specialist_profiles():
        live_match = live_by_id.get(profile.specialist_id)
        live_connected = bool(live_match and live_match.get("status") in {"online", "busy"})
        history = runs_by_role.get(profile.role, {"runs_total": 0, "runs_succeeded": 0, "runs_failed": 0})
        policy = policy_by_role.get(profile.role)
        specialist_profiles.append(
            {
                "specialist_id": profile.specialist_id,
                "role": profile.role,
                "domains": list(profile.domains),
                "keywords": list(profile.keywords),
                "capabilities": list(profile.capabilities),
                "description": profile.description,
                "trust_tier": policy["trust_tier"] if policy else "unregistered",
                "live_adapter_connected": live_connected,
                "layer": "specialist_template_metadata",
                "historical_runs": history,
            }
        )

    capabilities: list[dict[str, Any]] = []
    if capability_db is not None:
        cap_path = Path(capability_db)
        if not cap_path.is_symlink() and cap_path.is_file():
            with CapabilityRegistry(cap_path) as cap_reg:
                fields = (
                    "capability_id",
                    "domain",
                    "name",
                    "description",
                    "capabilities",
                    "requirements",
                    "trust",
                    "license",
                    "status",
                    "version",
                )
                capabilities = [{k: item.get(k) for k in fields} for item in cap_reg.list_all()]

    return {
        "commander": {
            "id": "muse_commander",
            "title": "Muse Commander · 唯一总调度中枢",
            "sole_authority": True,
            "competing_schedulers": 0,
            "max_workers_cap": 32,
            "queue_counts": queue_counts,
        },
        "registry_configured": registry_configured,
        "live_agents": live_agents,
        "role_policies": role_policies,
        "specialist_profiles": specialist_profiles,
        "orchestration_summary": {
            "total_runs": orchestration_total,
            "by_status": orchestration_by_status,
            "recent_runs": recent_runs,
        },
        "capabilities": capabilities,
        "resource_governor": ResourcePolicy().decide(probe_host_resources()).as_dict(),
    }


def preview_specialist_route(
    goal: str,
    *,
    task_db: str | Path | None = None,
    agent_db: str | Path | None = None,
) -> dict[str, Any]:
    """Read-only specialist scoring preview without dispatching any task or agent."""
    if not isinstance(goal, str) or not goal.strip() or len(goal.strip()) > 500:
        raise ValueError("goal must contain 1-500 non-whitespace characters")
    clean_goal = goal.strip()
    normal_goal = _normalise(clean_goal)

    _, live_agents = _read_live_agents(agent_db)
    live_by_id = {str(agent["agent_id"]): agent for agent in live_agents}

    policy_tiers: dict[str, str] = {}
    task_conn = _ro_connect(task_db)
    try:
        for row in _safe_rows(task_conn, "SELECT role, trust_tier FROM agent_role_policies"):
            policy_tiers[str(row["role"])] = str(row["trust_tier"])
    finally:
        if task_conn is not None:
            task_conn.close()

    considered: list[dict[str, Any]] = []
    for profile in builtin_specialist_profiles():
        if not profile.enabled:
            continue
        matched_domains = [term for term in profile.domains if _contains_term(normal_goal, _normalise(term))]
        matched_keywords = [term for term in profile.keywords if _contains_term(normal_goal, _normalise(term))]
        score = len(matched_domains) * 4 + len(matched_keywords) * 2 + profile.priority
        if score <= 0:
            continue
        live_match = live_by_id.get(profile.specialist_id)
        live_connected = bool(live_match and live_match.get("status") in {"online", "busy"})
        considered.append(
            {
                "specialist_id": profile.specialist_id,
                "role": profile.role,
                "description": profile.description,
                "score": score,
                "matched_domains": matched_domains,
                "matched_keywords": matched_keywords,
                "trust_tier": policy_tiers.get(profile.role, "unregistered"),
                "live_adapter_connected": live_connected,
                "layer": "live_registered_agent" if live_connected else "specialist_template_metadata",
            }
        )
    considered.sort(key=lambda item: (-item["score"], item["specialist_id"]))
    selected = considered[:4]
    return {
        "goal": clean_goal,
        "readonly_preview": True,
        "dispatched": False,
        "live_ready_count": sum(1 for item in considered if item["live_adapter_connected"]),
        "selected": selected,
        "considered": considered,
    }


def _probe_optional_engines() -> dict[str, dict[str, Any]]:
    checks = {
        "celestial_calendar": ("celestial_calendar", "celestial-calendar 历算桥接"),
        "qimen": ("qimen_rs", "qimen-rs 奇门排盘库"),
        "ziwei": ("ziwei", "ziwei 紫微排盘库"),
        "liu_ren": ("kinliuren", "kinliuren 大六壬排盘库"),
    }
    engines: dict[str, dict[str, Any]] = {}
    for key, (module_name, label) in checks.items():
        available = importlib.util.find_spec(module_name) is not None
        engines[key] = {
            "label": label,
            "status": "ready" if available else "unavailable",
            "reason": "本机 Python 环境已检测到模块" if available else f"未安装可选扩展 {module_name}（不影响 Muse 核心运作）",
        }
    mingli_model = os.environ.get("MUSE_MINGLI_MODEL", "").strip()
    engines["mingli_ai"] = {
        "label": "Mingli-AI 本地模型解释器",
        "status": "configured" if mingli_model else "unavailable",
        "reason": f"已配置模型 {mingli_model}" if mingli_model else "未启动/未配置 MUSE_MINGLI_MODEL",
    }
    return engines


def build_celestial_catalog(now: datetime | None = None) -> dict[str, Any]:
    """Return Bagua, 28 Star Mansions, deterministic sexagenary clock, and symbolic lenses."""
    current = now if now is not None else datetime.now().astimezone()
    if current.tzinfo is None or current.utcoffset() is None:
        current = current.astimezone()
    local_naive = current.replace(tzinfo=None)
    day_gz = sexagenary_day(local_naive.date())
    hour_branch = hour_branch_from_clock(local_naive.time())
    hour_gz = sexagenary_hour(day_gz[0], hour_branch)
    year_gz = sexagenary_year(local_naive.year)

    lenses = [
        {
            "id": key,
            "name": info["name"],
            "tradition": info["tradition"],
            "lens": info["lens"],
            "questions": list(info["questions"]),
        }
        for key, info in SYMBOLIC_LENSES.items()
    ]
    traditions = [
        {
            "id": key,
            "name": info["name"],
            "knowledge_scope": info["knowledge_scope"],
            "interpretive_layers": list(info["interpretive_layers"]),
            "school_variance_warning": info["school_variance_warning"],
        }
        for key, info in CORE_TRADITION_PROFILES.items()
    ]

    return {
        "epistemic_status": "symbolic_inspiration_only",
        "is_evidence": False,
        "is_prediction": False,
        "calendar_reference": {
            "local_iso": current.isoformat(),
            "year_ganzhi_civil": year_gz,
            "day_ganzhi": day_gz,
            "hour_branch": hour_branch,
            "hour_ganzhi": hour_gz,
            "basis_note": "公历日柱与时辰干支确定性参考（立春/节气交界精确换柱需可选 celestial-calendar）。",
            "reference_sources": list(REFERENCE_SOURCE_REFS),
        },
        "bagua_trigrams": [dict(item) for item in BAGUA_TRIGRAMS],
        "star_mansion_quadrants": STAR_MANSION_QUADRANTS,
        "star_mansions": [dict(item) for item in STAR_MANSIONS],
        "symbolic_lenses": lenses,
        "core_traditions": traditions,
        "optional_engines": _probe_optional_engines(),
    }


def run_yijing_inquiry(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute deterministic Yi Jing structure lookup + optional cross-tradition hypothesis lenses."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    mode = payload.get("mode", "seeded_cast")
    if mode not in {"seeded_cast", "manual_lines", "trigram_pair"}:
        raise ValueError("mode must be seeded_cast, manual_lines, or trigram_pair")

    if mode == "seeded_cast":
        seed = payload.get("seed", "")
        reading = seeded_three_coin_cast(seed)
    elif mode == "manual_lines":
        line_values = payload.get("line_values")
        reading = analyze_yijing_lines(line_values)
    else:
        lower_bits = payload.get("lower_bits")
        upper_bits = payload.get("upper_bits")
        if lower_bits not in TRIGRAM_BY_BITS or upper_bits not in TRIGRAM_BY_BITS:
            raise ValueError("lower_bits and upper_bits must be valid 3-bit trigram patterns")
        bits = str(lower_bits) + str(upper_bits)
        line_values = [7 if bit == "1" else 8 for bit in bits]
        reading = analyze_yijing_lines(line_values)
        reading["input_origin"] = "trigram_pair_lookup"
        reading["calculation_method"] = "trigram_pair_structural_lookup_v1"

    goal = payload.get("goal")
    cross_system = None
    lens_seeds = None
    if goal is not None:
        if not isinstance(goal, str):
            raise ValueError("goal must be a string")
        cleaned_goal = goal.strip()
        if cleaned_goal:
            if len(cleaned_goal) > 1000:
                raise ValueError("goal must be at most 1000 characters")
            raw_frameworks = payload.get("frameworks")
            frameworks = (
                tuple(raw_frameworks)
                if isinstance(raw_frameworks, list) and raw_frameworks
                else ("iching", "qimen", "ziwei", "liu_ren")
            )
            cross_system = build_cross_system_inspiration(cleaned_goal, frameworks=frameworks)
            pack = build_symbolic_inspiration_pack(cleaned_goal)
            lens_seeds = [item for item in pack if item["id"] in {"iching", "liuyao", "qimen", "ziwei"}]

    return {
        "mode": mode,
        "reading": reading,
        "cross_system": cross_system,
        "lens_seeds": lens_seeds,
        "epistemic_status": "symbolic_hypothesis_only",
        "is_evidence": False,
        "is_prediction": False,
        "automatic_memory_write": False,
        "automatic_task_approval": False,
        "notice": "玄学推演 / 待验证假设：仅展示象数结构与多体系研究提问，不代表预测、实证或指令授权。",
    }
