//! Cross-version upgrade safety: before a new client version first starts the engine, copy
//! every user SQLite database (plus `-wal` / `-shm` companions) into
//! `<data root>/backups/<old>-to-<new>-<unix time>/`, preserving relative paths.
//! The engine is not running yet at this point, so plain file copies are consistent.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

const VERSION_MARKER: &str = ".muse-client-version";
const KEEP_BACKUPS: usize = 10;
const DB_SUFFIXES: [&str; 3] = [".sqlite3", ".sqlite", ".db"];
const COMPANION_SUFFIXES: [&str; 2] = ["-wal", "-shm"];

fn is_database(path: &Path) -> bool {
    let name = path
        .file_name()
        .map(|n| n.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    DB_SUFFIXES.iter().any(|suffix| name.ends_with(suffix))
        || COMPANION_SUFFIXES.iter().any(|companion| {
            DB_SUFFIXES
                .iter()
                .any(|suffix| name.ends_with(&format!("{suffix}{companion}")))
        })
}

fn collect(dir: &Path, skip: &Path, out: &mut Vec<PathBuf>) -> io::Result<()> {
    if !dir.is_dir() || dir.starts_with(skip) {
        return Ok(());
    }
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let path = entry.path();
        let kind = entry.file_type()?;
        if kind.is_symlink() {
            continue;
        }
        if kind.is_dir() {
            collect(&path, skip, out)?;
        } else if kind.is_file() && is_database(&path) {
            out.push(path);
        }
    }
    Ok(())
}

fn sanitize(version: &str) -> String {
    version
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '.' || c == '-' { c } else { '_' })
        .collect()
}

fn prune(backups: &Path) -> io::Result<()> {
    let mut dirs: Vec<PathBuf> = fs::read_dir(backups)?
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| {
            path.is_dir()
                && path
                    .file_name()
                    .map(|n| n.to_string_lossy().contains("-to-"))
                    .unwrap_or(false)
        })
        .collect();
    if dirs.len() <= KEEP_BACKUPS {
        return Ok(());
    }
    dirs.sort_by_key(|path| {
        fs::metadata(path)
            .and_then(|meta| meta.modified())
            .unwrap_or(UNIX_EPOCH)
    });
    let excess = dirs.len() - KEEP_BACKUPS;
    for old in dirs.into_iter().take(excess) {
        let _ = fs::remove_dir_all(old);
    }
    Ok(())
}

/// Returns `Ok(Some(dir))` when a backup was taken, `Ok(None)` when no upgrade happened.
pub fn backup_before_upgrade(data_dir: &Path, current_version: &str) -> io::Result<Option<PathBuf>> {
    fs::create_dir_all(data_dir)?;
    let marker = data_dir.join(VERSION_MARKER);
    let previous = fs::read_to_string(&marker)
        .ok()
        .map(|text| text.trim().to_string())
        .filter(|text| !text.is_empty());
    let result = match previous.as_deref() {
        Some(old) if old != current_version => {
            let backups = data_dir.join("backups");
            let mut files = Vec::new();
            collect(&data_dir.join("data"), &backups, &mut files)?;
            if files.is_empty() {
                None
            } else {
                let stamp = SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .map(|d| d.as_secs())
                    .unwrap_or(0);
                let target = backups.join(format!(
                    "{}-to-{}-{stamp}",
                    sanitize(old),
                    sanitize(current_version)
                ));
                for file in &files {
                    let relative = file.strip_prefix(data_dir).unwrap_or(file.as_path());
                    let destination = target.join(relative);
                    if let Some(parent) = destination.parent() {
                        fs::create_dir_all(parent)?;
                    }
                    fs::copy(file, &destination)?;
                }
                let manifest: Vec<String> = files
                    .iter()
                    .map(|f| {
                        f.strip_prefix(data_dir)
                            .unwrap_or(f.as_path())
                            .to_string_lossy()
                            .replace('\\', "/")
                    })
                    .collect();
                fs::write(
                    target.join("MANIFEST.txt"),
                    format!(
                        "Muse 升级前自动备份\nfrom={old}\nto={current_version}\nfiles={}\n{}\n",
                        manifest.len(),
                        manifest.join("\n")
                    ),
                )?;
                let _ = prune(&backups);
                Some(target)
            }
        }
        _ => None,
    };
    fs::write(&marker, current_version)?;
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_root(tag: &str) -> PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let dir = std::env::temp_dir().join(format!("muse-backup-test-{tag}-{stamp}"));
        fs::create_dir_all(&dir).expect("temp dir");
        dir
    }

    #[test]
    fn first_run_only_writes_marker() {
        let root = temp_root("first");
        assert!(backup_before_upgrade(&root, "0.1.0").expect("ok").is_none());
        assert_eq!(fs::read_to_string(root.join(VERSION_MARKER)).unwrap(), "0.1.0");
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn upgrade_copies_databases_and_companions() {
        let root = temp_root("upgrade");
        fs::create_dir_all(root.join("data/tasks")).unwrap();
        fs::write(root.join("data/tasks/muse.sqlite3"), b"db").unwrap();
        fs::write(root.join("data/tasks/muse.sqlite3-wal"), b"wal").unwrap();
        fs::write(root.join("data/tasks/notes.txt"), b"skip").unwrap();
        fs::write(root.join(VERSION_MARKER), "0.0.9").unwrap();
        let target = backup_before_upgrade(&root, "0.1.0").expect("ok").expect("backup taken");
        assert!(target.join("data/tasks/muse.sqlite3").is_file());
        assert!(target.join("data/tasks/muse.sqlite3-wal").is_file());
        assert!(!target.join("data/tasks/notes.txt").exists());
        assert!(backup_before_upgrade(&root, "0.1.0").expect("ok").is_none());
        let _ = fs::remove_dir_all(root);
    }
}
