//! 中文系统托盘：打开主控台 / 待签名审查台 / 快速记灵感 / 暂停自主任务 /
//! 紧急熔断停止 (Kill Switch) / 安全退出。

use std::thread;

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};

use crate::sidecar;

const TRAY_ID: &str = "muse-tray";

pub fn install(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "打开主控台", true, None::<&str>)?;
    let review = MenuItem::with_id(app, "review", "待签名审查台", true, None::<&str>)?;
    let note = MenuItem::with_id(app, "note", "快速记灵感", true, None::<&str>)?;
    let separator_a = PredefinedMenuItem::separator(app)?;
    let pause = MenuItem::with_id(app, "pause", "暂停自主任务", true, None::<&str>)?;
    let kill = MenuItem::with_id(app, "kill", "紧急熔断停止（Kill Switch）", true, None::<&str>)?;
    let separator_b = PredefinedMenuItem::separator(app)?;
    let quit = MenuItem::with_id(app, "quit", "安全退出", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[
            &open,
            &review,
            &note,
            &separator_a,
            &pause,
            &kill,
            &separator_b,
            &quit,
        ],
    )?;

    let mut builder = TrayIconBuilder::with_id(TRAY_ID)
        .tooltip("Muse 星枢 · 自主守护默认关闭")
        .menu(&menu)
        .on_menu_event(|app, event| handle_menu(app, event.id().as_ref()));
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder.build(app)?;
    Ok(())
}

fn set_tooltip(app: &AppHandle, text: &str) {
    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        let _ = tray.set_tooltip(Some(text));
    }
}

fn handle_menu(app: &AppHandle, id: &str) {
    match id {
        "open" => {
            sidecar::navigate_main(app, "");
        }
        "review" => {
            sidecar::navigate_main(app, "review-desk.html");
        }
        "note" => open_quick_note(app),
        "pause" => control(
            app.clone(),
            r#"{"action":"pause","reason":"托盘：暂停自主任务"}"#,
            "已暂停自主任务",
        ),
        "kill" => control(
            app.clone(),
            r#"{"action":"kill","confirmation":"KILL-SWITCH","reason":"托盘：紧急熔断停止"}"#,
            "已触发紧急熔断（Kill Switch）",
        ),
        "quit" => {
            sidecar::shutdown(app);
            app.exit(0);
        }
        _ => {}
    }
}

fn open_quick_note(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("quicknote") {
        let _ = window.show();
        let _ = window.set_focus();
        return;
    }
    let Some(url) = sidecar::engine_url(app, "__muse/quick-note.html") else {
        set_tooltip(app, "Muse 引擎尚未就绪，请稍后再记灵感");
        return;
    };
    let built = WebviewWindowBuilder::new(app, "quicknote", WebviewUrl::External(url))
        .title("快速记灵感 · Muse")
        .inner_size(440.0, 380.0)
        .resizable(true)
        .always_on_top(true)
        .build();
    if let Err(error) = built {
        set_tooltip(app, &format!("无法打开快速记灵感：{error}"));
    }
}

/// Safety controls run off the UI thread; the daemon is also asked to stop.
fn control(app: AppHandle, body: &'static str, done: &'static str) {
    thread::spawn(move || {
        let safety = sidecar::post_api(&app, "/api/v1/omega/safety/control", body);
        let _ = sidecar::post_api(&app, "/api/v1/daemon/stop", "{}");
        match safety {
            Ok((status, _)) if (200..300).contains(&status) => {
                set_tooltip(&app, &format!("Muse 星枢 · {done}"));
            }
            Ok((status, text)) => {
                let snippet: String = text.chars().take(80).collect();
                set_tooltip(&app, &format!("Muse 星枢 · 操作失败 HTTP {status} {snippet}"));
            }
            Err(error) => set_tooltip(&app, &format!("Muse 星枢 · 操作失败：{error}")),
        }
    });
}
