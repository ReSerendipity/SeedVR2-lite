//! 文件拖拽支持（对应指导文档任务 4）。
//!
//! 数据流：
//! - Tauri 原生把 `tauri://drag-drop`（含**本地绝对路径**）转发给 WebView，
//!   由前端桥接脚本 `desktop-bridge.js` 消费并区分文件/文件夹：
//!   单文件 → 调 [`read_dragged_file`] 读字节构造 File 填入上传区并自动开始；
//!   多文件/文件夹 → 切批量模式并授权扫描目录（HTML5 drop 拿不到真实路径）。
//! - Rust 侧职责：在 main.rs 的 `on_webview_event` 把拖入路径登记进 [`DraggedFiles`]
//!   白名单，[`read_dragged_file`] 只允许读取白名单内的文件，
//!   防止 WebView 借该命令任意读取磁盘。
//!
//! 拖放事件 payload 与 Tauri 转发给前端的一致：`{ paths: string[], position }`。

use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use tauri::State;

/// 允许读取的拖入文件白名单（防止 WebView 借命令任意读盘）
#[derive(Default)]
pub struct DraggedFiles {
    allowed: Mutex<HashSet<PathBuf>>,
}

impl DraggedFiles {
    /// 登记一批拖入路径（文件与目录都登记）
    pub fn allow_paths(&self, paths: impl IntoIterator<Item = PathBuf>) {
        let mut set = self.allowed.lock().unwrap();
        for p in paths {
            set.insert(p);
        }
        // 容量保护：只保留最近 512 条
        if set.len() > 512 {
            set.clear();
        }
    }

    fn is_allowed(&self, path: &Path) -> bool {
        let set = self.allowed.lock().unwrap();
        set.iter().any(|p| p == path || (p.is_dir() && path.starts_with(p)))
    }
}

/// 前端命令：读取拖入过的文件字节（base64），供桥接脚本构造 File 对象。
#[tauri::command]
pub fn read_dragged_file(
    path: String,
    store: State<'_, DraggedFiles>,
) -> Result<String, String> {
    let p = PathBuf::from(&path);
    if !store.is_allowed(&p) {
        return Err("该文件未经过拖放授权，拒绝读取".into());
    }
    if !p.is_file() {
        return Err("路径不是文件".into());
    }
    let bytes = fs::read(&p).map_err(|e| format!("读取失败: {e}"))?;
    // 图片体量保护：单文件上限 200MB
    if bytes.len() > 200 * 1024 * 1024 {
        return Err("文件超过 200MB，无法通过拖拽处理".into());
    }
    use base64::Engine as _;
    Ok(base64::engine::general_purpose::STANDARD.encode(&bytes))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn whitelist_allows_registered_and_denies_others() {
        let store = DraggedFiles::default();
        store.allow_paths(vec![PathBuf::from("C:/imgs/a.png")]);
        assert!(store.is_allowed(Path::new("C:/imgs/a.png")));
        assert!(!store.is_allowed(Path::new("C:/Windows/system32/config/sam")));
    }

    #[test]
    fn whitelist_dir_covers_children() {
        let store = DraggedFiles::default();
        let dir = std::env::temp_dir().join(format!("seedvr2_drag_{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        store.allow_paths(vec![dir.clone()]);
        assert!(store.is_allowed(&dir.join("sub/x.png")));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn whitelist_clears_when_overflow() {
        let store = DraggedFiles::default();
        let many: Vec<PathBuf> = (0..600).map(|i| PathBuf::from(format!("C:/p{i}.png"))).collect();
        store.allow_paths(many);
        assert!(store.allowed.lock().unwrap().is_empty(), "超容量应清空");
    }
}
