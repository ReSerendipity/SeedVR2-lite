use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use anyhow::{Result, anyhow};
use serde::Serialize;
use tauri::{AppHandle, Emitter};

use crate::port_manager::find_free_port;

/// Python 进程状态
#[derive(Debug, Clone, Serialize)]
pub enum PythonStatus {
    Stopped,
    Starting,
    Running,
    Crashed,
}

/// Python 子进程管理器
pub struct PythonProcess {
    child: Option<Child>,
    port: u16,
    runtime_dir: PathBuf,
    app_dir: PathBuf,
    log_dir: PathBuf,
    status: PythonStatus,
    restart_count: u32,
    max_restarts: u32,
}

impl PythonProcess {
    pub fn new(runtime_dir: PathBuf, app_dir: PathBuf, log_dir: PathBuf) -> Self {
        Self {
            child: None,
            port: 0,
            runtime_dir,
            app_dir,
            log_dir,
            status: PythonStatus::Stopped,
            restart_count: 0,
            max_restarts: 3,
        }
    }

    /// 启动 Python 子进程
    pub fn start(&mut self) -> Result<u16> {
        // 找空闲端口
        let port = find_free_port()?;
        self.port = port;

        // 确定 Python 可执行文件路径
        let python_exe = self.runtime_dir.join("python.exe");
        if !python_exe.exists() {
            return Err(anyhow!("Python 可执行文件不存在: {}", python_exe.display()));
        }

        // 启动脚本
        let start_script = self.app_dir.join("start_portable.py");
        if !start_script.exists() {
            return Err(anyhow!("启动脚本不存在: {}", start_script.display()));
        }

        // 确保日志目录存在
        std::fs::create_dir_all(&self.log_dir)?;
        let log_file = self.log_dir.join(format!(
            "python_{}.log",
            chrono::Local::now().format("%Y%m%d_%H%M%S")
        ));
        let log_writer = std::fs::File::create(&log_file)?;

        // 启动子进程
        let child = Command::new(&python_exe)
            .arg(&start_script)
            .arg("--port")
            .arg(port.to_string())
            .arg("--host")
            .arg("127.0.0.1")
            .current_dir(&self.app_dir)
            .stdout(Stdio::from(log_writer.try_clone()?))
            .stderr(Stdio::from(log_writer))
            .env("PYTHONNOUSERSITE", "1")
            .env("PYTHONUNBUFFERED", "1")
            .spawn()?;

        self.child = Some(child);
        self.status = PythonStatus::Starting;
        log::info!("Python 进程已启动，PID={}, 端口={}", self.child.as_ref().unwrap().id(), port);

        Ok(port)
    }

    /// 标记为运行中
    pub fn mark_running(&mut self) {
        self.status = PythonStatus::Running;
        self.restart_count = 0;
    }

    /// 检查进程是否存活
    pub fn is_alive(&mut self) -> bool {
        if let Some(child) = &mut self.child {
            match child.try_wait() {
                Ok(None) => true,
                Ok(Some(status)) => {
                    log::warn!("Python 进程已退出，状态={}", status);
                    false
                }
                Err(_) => false,
            }
        } else {
            false
        }
    }

    /// 尝试重启（受 max_restarts 限制）
    pub fn try_restart(&mut self) -> Result<bool> {
        if self.restart_count >= self.max_restarts {
            self.status = PythonStatus::Crashed;
            return Ok(false);
        }
        self.restart_count += 1;
        log::info!("正在重启 Python 进程（第 {}/{} 次）", self.restart_count, self.max_restarts);
        self.stop()?;
        self.start()?;
        Ok(true)
    }

    /// 停止 Python 进程（Windows 下杀整棵进程树：uvicorn/torch 会 spawn 子进程，
    /// 只杀直接子进程会留下孤儿持有 app/ 文件句柄，导致更新换载 rename 失败）
    pub fn stop(&mut self) -> Result<()> {
        if let Some(mut child) = self.child.take() {
            let pid = child.id();
            #[cfg(windows)]
            {
                // taskkill /T 级联终止子进程树；失败再回退 kill()
                let ok = Command::new("taskkill")
                    .args(["/PID", &pid.to_string(), "/T", "/F"])
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status()
                    .map(|s| s.success())
                    .unwrap_or(false);
                if !ok {
                    let _ = child.kill();
                }
            }
            #[cfg(not(windows))]
            {
                let _ = child.kill();
            }
            let _ = child.wait();
            log::info!("Python 进程树已停止（根 PID={pid}）");
        }
        self.status = PythonStatus::Stopped;
        Ok(())
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    pub fn status(&self) -> PythonStatus {
        self.status.clone()
    }
}

/// 全局共享的 Python 进程管理器
pub struct PythonState {
    pub process: Mutex<PythonProcess>,
}

/// 启动事件负载
#[derive(Serialize, Clone)]
struct StartupStatus {
    message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

/// 向发送启动状态
pub fn emit_startup_status(app: &AppHandle, message: &str, error: Option<String>) {
    let _ = app.emit("startup-status", StartupStatus {
        message: message.to_string(),
        error,
    });
}

/// 解析运行时目录：优先侧载 runtime，开发模式回退项目 .venv，再退系统 Python
pub fn resolve_runtime_dir(app_dir: &Path) -> PathBuf {
    // 1. 打包后：应用目录下的 runtime/
    let bundled = app_dir.join("runtime");
    if bundled.join("python.exe").exists() {
        return bundled;
    }

    // 2. 开发模式 A：app_dir 即项目根（resolve_app_dir 的开发分支），.venv 在其下
    let dev_local = app_dir.join(".venv").join("Scripts");
    if dev_local.join("python.exe").exists() {
        return dev_local;
    }

    // 3. 开发模式 B：app_dir 为 exe_dir/app，.venv 位于上两级（历史布局）
    let dev_venv = app_dir
        .parent()
        .and_then(|p| p.parent())
        .map(|p| p.join(".venv").join("Scripts"));
    if let Some(venv) = dev_venv {
        if venv.join("python.exe").exists() {
            return venv;
        }
    }

    // 4. 回退：系统 PATH 中的 python
    PathBuf::from("python")
}

/// 解析应用代码目录
pub fn resolve_app_dir() -> PathBuf {
    // 开发模式：项目根目录
    let dev_app = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .to_path_buf();
    if dev_app.join("start_portable.py").exists() {
        return dev_app;
    }

    // 打包后：当前可执行文件目录下的 app/
    if let Ok(exe) = std::env::current_exe() {
        let bundled = exe.parent().unwrap().join("app");
        if bundled.exists() {
            return bundled;
        }
    }

    dev_app
}
