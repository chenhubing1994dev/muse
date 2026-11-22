use std::{
    ffi::OsString,
    path::{Path, PathBuf},
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PythonCommand {
    Executable(PathBuf),
    Launcher {
        program: OsString,
        args: Vec<OsString>,
    },
}

/// Resolve the Python command without invoking a shell. The target is explicit
/// so platform selection can be tested on any host.
pub fn resolve_python_command(
    root: &Path,
    target: &str,
    override_path: Option<OsString>,
) -> PythonCommand {
    if let Some(path) = override_path.filter(|value| !value.is_empty()) {
        return PythonCommand::Executable(PathBuf::from(path));
    }

    match target {
        "windows" => {
            let venv = root.join(".venv").join("Scripts").join("python.exe");
            if venv.is_file() {
                PythonCommand::Executable(venv)
            } else {
                PythonCommand::Launcher {
                    program: OsString::from("py"),
                    args: vec![OsString::from("-3.11")],
                }
            }
        }
        "macos" => {
            let venv = root.join(".venv").join("bin").join("python3");
            if venv.is_file() {
                PythonCommand::Executable(venv)
            } else {
                PythonCommand::Launcher {
                    program: OsString::from("python3"),
                    args: Vec::new(),
                }
            }
        }
        "linux" => {
            let venv = root.join(".venv").join("bin").join("python3");
            if venv.is_file() {
                PythonCommand::Executable(venv)
            } else {
                PythonCommand::Launcher {
                    program: OsString::from("python3"),
                    args: Vec::new(),
                }
            }
        }
        _ => PythonCommand::Launcher {
            program: OsString::from("python3"),
            args: Vec::new(),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn explicit_override_has_highest_priority() {
        let root = Path::new("missing-project");
        assert_eq!(
            resolve_python_command(root, "windows", Some(OsString::from("custom-python"))),
            PythonCommand::Executable(PathBuf::from("custom-python"))
        );
    }

    #[test]
    fn windows_falls_back_to_python_launcher() {
        let root = Path::new("missing-project");
        assert_eq!(
            resolve_python_command(root, "windows", None),
            PythonCommand::Launcher {
                program: OsString::from("py"),
                args: vec![OsString::from("-3.11")]
            }
        );
    }

    fn write_fake_python(root: &Path, relative_path: &str) -> PathBuf {
        let executable = root.join(relative_path);
        std::fs::create_dir_all(executable.parent().expect("python executable has a parent"))
            .expect("create virtual environment directories");
        std::fs::write(&executable, b"").expect("create fake python executable");
        executable
    }

    #[test]
    fn windows_prefers_project_virtual_environment() {
        let root = std::env::temp_dir().join(format!(
            "muse-python-resolver-{}-windows",
            std::process::id()
        ));
        let executable = write_fake_python(&root, ".venv/Scripts/python.exe");
        assert_eq!(
            resolve_python_command(&root, "windows", None),
            PythonCommand::Executable(executable)
        );
        std::fs::remove_dir_all(root).expect("remove temporary project");
    }

    #[test]
    fn macos_prefers_project_virtual_environment() {
        let root =
            std::env::temp_dir().join(format!("muse-python-resolver-{}-macos", std::process::id()));
        let executable = write_fake_python(&root, ".venv/bin/python3");
        assert_eq!(
            resolve_python_command(&root, "macos", None),
            PythonCommand::Executable(executable)
        );
        std::fs::remove_dir_all(root).expect("remove temporary project");
    }

    #[test]
    fn macos_never_uses_windows_py_launcher() {
        let root = Path::new("missing-project");
        assert_eq!(
            resolve_python_command(root, "macos", None),
            PythonCommand::Launcher {
                program: OsString::from("python3"),
                args: Vec::new()
            }
        );
    }

    #[test]
    fn linux_prefers_project_virtual_environment() {
        let root =
            std::env::temp_dir().join(format!("muse-python-resolver-{}-linux", std::process::id()));
        let executable = write_fake_python(&root, ".venv/bin/python3");
        assert_eq!(
            resolve_python_command(&root, "linux", None),
            PythonCommand::Executable(executable)
        );
        std::fs::remove_dir_all(root).expect("remove temporary project");
    }

    #[test]
    fn linux_falls_back_to_python3_without_windows_launcher() {
        let root = Path::new("missing-project");
        assert_eq!(
            resolve_python_command(root, "linux", None),
            PythonCommand::Launcher {
                program: OsString::from("python3"),
                args: Vec::new()
            }
        );
    }

    #[test]
    fn linux_respects_explicit_python_override() {
        let root = Path::new("missing-project");
        assert_eq!(
            resolve_python_command(root, "linux", Some(OsString::from("custom-python"))),
            PythonCommand::Executable(PathBuf::from("custom-python"))
        );
    }
}
