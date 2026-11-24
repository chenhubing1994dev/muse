# Muse Server（Rust 外壳）

单用户、本机/可信网络优先的 Muse 远程服务外壳。它**不是第二套调度器**：状态、任务、审批、记忆、研究、成果与执行全部由现有 Python 运行时（`status_api.py` / `web_portal.py` / `muse.autonomous_runtime`）负责，Rust 只做静态托管、API 反向代理与受管子进程生命周期。

设计文档：`docs/superpowers/specs/2026-11-24-muse-server-rust-design.md`

## 运行

```bash
# 默认：绑定 127.0.0.1:8767，代理到 127.0.0.1:8766
muse-server

# 自定义
MUSE_SERVER_BIND=127.0.0.1:9000 \
MUSE_SERVER_UPSTREAM=http://127.0.0.1:8766 \
MUSE_SERVER_PORTAL_DIR=./web-portal \
MUSE_PYTHON=python3 \
MUSE_PROJECT_ROOT=/path/to/Muse \
MUSE_SERVER_AUTONOMY=1 \
muse-server
```

健康检查：

- `GET /healthz` → 服务自身状态
- `GET /readyz` → 上游 Python API 是否可用
- `GET /livez` → 存活探针
- `GET /api/v1/server/info` → 服务信息与执行权威标记
- `GET /ui/*` → `web-portal/` 静态资源

所有 `/api/v1/*` 请求被透明代理到上游 Python API，Authorization/Cookie/CSRF 原样转发；鉴权权威仍是 `PortalSessionStore`。API 响应强制 `Cache-Control: no-store`。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `MUSE_SERVER_BIND` | `127.0.0.1:8767` | 监听地址，默认仅 loopback |
| `MUSE_SERVER_UPSTREAM` | `http://127.0.0.1:8766` | Python API 地址 |
| `MUSE_SERVER_PORTAL_DIR` | `../web-portal` | 静态门户目录 |
| `MUSE_PYTHON` | `python3` | 受管子进程使用的 Python |
| `MUSE_PROJECT_ROOT` | 当前目录上一级 | 传给子进程的项目根目录 |
| `MUSE_SERVER_AUTONOMY` | `false` | 启动时是否拉起并监管自主运行子进程 |
| `MUSE_SERVER_PUBLIC_URL` | 空 | 公网 URL；设置时需前置 TLS 反向代理 |

## 安全边界

- 首版单用户；公网开放与多租户为后续版本。
- 只 kill 本进程自己 spawn 的自主运行子进程，不触碰外部 LM Studio / ComfyUI。
- 不直接读写业务 SQLite，不暴露任意文件路径；成果下载仍走现有产品库白名单代理。
- 玄学内容只作灵感假设；AI 视频仍受 Commander 审批门禁约束；不自动发布、不自动付款。

## 发布包

由 `.github/workflows/scheduled-release.yml` 的 `server` job 构建，覆盖 Linux x86_64/aarch64、Windows x86_64、macOS Intel/ARM，产物为 `muse-server-*.tar.gz` / `.zip`，内含二进制、`web-portal/` 静态资源、配置说明与 `.sha256`。
