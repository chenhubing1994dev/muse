use std::net::SocketAddr;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;

use axum::body::Body;
use axum::extract::State;
use axum::http::{header, HeaderMap, Method, Request, StatusCode, Uri};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use clap::Parser;
use serde_json::json;
use tokio::net::TcpListener;
use tokio::process::{Child, Command};
use tokio::sync::Mutex;
use tower_http::services::ServeDir;
use tracing::{info, warn};

/// Muse Rust server shell.
///
/// Responsibilities (first iteration):
/// - serve the existing `web-portal/` static files under `/ui`
/// - reverse-proxy `/api/v1/*` to the Python Status API / Web Portal
/// - own the lifecycle of the Python autonomous-runtime child process it spawns
/// - expose health/ready/liveness without embedding business logic
///
/// It intentionally does NOT write business SQLite or create a second
/// scheduler/queue/auth system. Commander and the Python runtime remain authoritative.
#[derive(Debug, Parser)]
#[command(name = "muse-server", version, about)]
struct Config {
    /// Address to bind. Defaults to loopback only.
    #[arg(long, env = "MUSE_SERVER_BIND", default_value = "127.0.0.1:8767")]
    bind: String,

    /// Base URL of the Python API to proxy.
    #[arg(long, env = "MUSE_SERVER_UPSTREAM", default_value = "http://127.0.0.1:8766")]
    upstream: String,

    /// Path to web-portal static files.
    #[arg(long, env = "MUSE_SERVER_PORTAL_DIR", default_value = "../web-portal")]
    portal_dir: PathBuf,

    /// Python executable used for managed child processes.
    #[arg(long, env = "MUSE_PYTHON", default_value = "python3")]
    python: String,

    /// Project root passed to child processes.
    #[arg(long, env = "MUSE_PROJECT_ROOT")]
    project_root: Option<PathBuf>,

    /// Start and supervise the Python autonomous runtime on server boot.
    #[arg(long, env = "MUSE_SERVER_AUTONOMY", default_value_t = false)]
    autonomy: bool,

    /// Public URL for deployment checks. Empty means local/trusted-network mode.
    #[arg(long, env = "MUSE_SERVER_PUBLIC_URL", default_value = "")]
    public_url: String,
}

#[derive(Clone)]
struct AppState {
    upstream: String,
    http: reqwest::Client,
    managed_child: Arc<Mutex<Option<Child>>>,
    started_unix: u64,
}

fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    if let Err(err) = run() {
        warn!("muse-server exited with error: {err}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let config = Config::parse();
    let addr: SocketAddr = config.bind.parse()?;

    if !config.public_url.is_empty() {
        info!(
            "public_url configured; terminate TLS at a trusted reverse proxy (server binds {})",
            addr
        );
    }

    let project_root = config.project_root.clone().unwrap_or_else(|| {
        std::env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join("..")
    });

    let started_unix = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);

    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;

    runtime.block_on(async move {
        let state = AppState {
            upstream: config.upstream.trim_end_matches('/').to_string(),
            http: reqwest::Client::builder().no_proxy().build()?,
            managed_child: Arc::new(Mutex::new(None)),
            started_unix,
        };

        if config.autonomy {
            let child = Command::new(&config.python)
                .arg("-m")
                .arg("muse.autonomous_runtime")
                .current_dir(&project_root)
                .env("PYTHONPATH", project_root.join("src"))
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn();

            match child {
                Ok(child) => {
                    info!("spawned managed autonomous runtime pid={:?}", child.id());
                    *state.managed_child.lock().await = Some(child);
                }
                Err(err) => warn!("failed to spawn autonomous runtime: {err}"),
            }
        }

        let static_files = ServeDir::new(&config.portal_dir);
        let app = Router::new()
            .route("/healthz", get(healthz))
            .route("/readyz", get(readyz))
            .route("/livez", get(livez))
            .route("/api/v1/server/info", get(server_info))
            .nest_service("/ui", static_files)
            .fallback(proxy_or_static)
            .with_state(state.clone());

        info!("muse-server listening on {addr}, upstream={}", config.upstream);
        let listener = TcpListener::bind(addr).await?;
        axum::serve(listener, app)
            .with_graceful_shutdown(shutdown_signal())
            .await?;

        // Stop only the child this process created. Never touch external
        // LM Studio / ComfyUI / other user-managed processes.
        if let Some(mut child) = state.managed_child.lock().await.take() {
            let _ = child.kill().await;
            info!("stopped managed autonomous runtime");
        }
        Ok::<(), Box<dyn std::error::Error>>(())
    })
}

async fn shutdown_signal() {
    #[cfg(unix)]
    {
        use tokio::signal::unix::{signal, SignalKind};
        let mut term = signal(SignalKind::terminate()).expect("install SIGTERM handler");
        tokio::select! {
            _ = tokio::signal::ctrl_c() => info!("SIGINT received"),
            _ = term.recv() => info!("SIGTERM received"),
        }
    }
    #[cfg(not(unix))]
    {
        let _ = tokio::signal::ctrl_c().await;
        info!("Ctrl+C received");
    }
}

async fn healthz(State(state): State<AppState>) -> Response {
    (
        StatusCode::OK,
        Json(json!({"status":"ok","started_unix":state.started_unix})),
    )
        .into_response()
}

async fn readyz(State(state): State<AppState>) -> Response {
    let target = format!("{}/api/v1/health", state.upstream);
    match state.http.get(&target).send().await {
        Ok(resp) if resp.status().is_success() => {
            (StatusCode::OK, Json(json!({"ready":true,"upstream":"ok"}))).into_response()
        }
        Ok(resp) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"ready":false,"upstream_status":resp.status().as_u16()})),
        )
            .into_response(),
        Err(err) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"ready":false,"upstream_error":err.to_string()})),
        )
            .into_response(),
    }
}

async fn livez() -> Response {
    (StatusCode::OK, Json(json!({"alive":true}))).into_response()
}

async fn server_info(State(state): State<AppState>) -> Response {
    let managed_autonomy = state.managed_child.lock().await.is_some();
    Json(json!({
        "service":"muse-server",
        "version":env!("CARGO_PKG_VERSION"),
        "upstream":state.upstream,
        "managed_autonomy":managed_autonomy,
        "started_unix":state.started_unix,
        "execution_authority":"python-commander",
    }))
    .into_response()
}

async fn proxy_or_static(State(state): State<AppState>, req: Request<Body>) -> Response {
    let path = req.uri().path().to_string();
    if !path.starts_with("/api/v1/") {
        return (StatusCode::NOT_FOUND, "not found").into_response();
    }

    let target: Uri = match format!("{}{}", state.upstream, req.uri()).parse() {
        Ok(uri) => uri,
        Err(_) => return StatusCode::BAD_GATEWAY.into_response(),
    };

    let method: Method = req.method().clone();
    let headers: HeaderMap = req.headers().clone();
    let body = req.into_body();
    let bytes = match axum::body::to_bytes(body, usize::MAX).await {
        Ok(bytes) => bytes,
        Err(_) => return StatusCode::BAD_REQUEST.into_response(),
    };

    let upstream_req = state
        .http
        .request(method, target.to_string())
        .headers(headers)
        .body(bytes);

    match upstream_req.send().await {
        Ok(resp) => {
            let status = resp.status();
            let content_type = resp.headers().get(header::CONTENT_TYPE).cloned();
            let mut builder = Response::builder()
                .status(status.as_u16())
                .header(header::CACHE_CONTROL, "no-store");
            if let Some(value) = content_type {
                builder = builder.header(header::CONTENT_TYPE, value);
            }
            match resp.bytes().await {
                Ok(bytes) => builder.body(Body::from(bytes)).unwrap().into_response(),
                Err(err) => (StatusCode::BAD_GATEWAY, err.to_string()).into_response(),
            }
        }
        Err(err) => (
            StatusCode::BAD_GATEWAY,
            format!("upstream unavailable: {err}"),
        )
            .into_response(),
    }
}
