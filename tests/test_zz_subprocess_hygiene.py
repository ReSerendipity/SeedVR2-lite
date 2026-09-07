"""测试套件卫生守门用例（2026-09-07 发布评估后续建议落地，GOTCHAS 对应批次）。

本仓多个测试文件（test_ffmpeg_lineage / test_gpu_observability / test_settings_routes /
test_video_processor / test_secret_key_and_manifest_signature）用 `mock.patch.object` /
`monkeypatch.setattr` 给 `subprocess` 模块对象打补丁——模块属性链解析到的是**全局唯一的
subprocess 模块**，这类 patch 本质是全局效应；当前全部为「作用域内自动恢复」写法（无泄漏），
但一旦未来引入裸赋值（`subprocess.run = fake` 不恢复）或忘关 patcher，同 pytest 进程内
所有后续真实子进程调用会被静默替换——CI Windows 上「stdout 静默丢失为 None」正是这一
失效模式的真实病例（KNOWN_ISSUES #76）。

本文件以 `test_zz_` 前缀保证收集排序最后，兜底捕获常驻污染：断言 subprocess 的关键入口
仍是标准库对象本身。
"""

import inspect
import subprocess


def test_subprocess_run_not_globally_polluted():
    assert (
        inspect.getmodule(subprocess.run) is subprocess
    ), "subprocess.run 被非标准库对象污染且未恢复——检查是否有测试裸赋值或忘关 patcher"


def test_subprocess_popen_not_globally_polluted():
    assert inspect.getmodule(subprocess.Popen) is subprocess, "subprocess.Popen 被非标准库对象污染且未恢复"
