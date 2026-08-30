#!/usr/bin/env python3
"""generate_config_reference.py — 从 Pydantic 配置模型自动生成配置参考文档。

背景（DX 评估 P2-9）：config.yaml 是机器序列化产物（0 注释），80+ 个键的
含义只能去读 config_models.py；本脚本把「键路径 / 类型 / 默认值 / 段落说明」
提取为 website/docs/guide/config.md，让配置解释有一个始终与代码同步的出口。

**不改 config.yaml**（该文件在 AI 禁区名单，且由 Pydantic 校验兜底）。

用法：
    python scripts/generate_config_reference.py            # 生成/覆盖 config.md
    python scripts/generate_config_reference.py --check    # 仅校验是否过期（CI 可用）

输出：website/docs/guide/config.md（VitePress 页面，勿手改）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import get_args, get_type_hints

from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = PROJECT_ROOT / "website" / "docs" / "guide" / "config.md"


def _load_app_config_model() -> type[BaseModel]:
    """延迟导入配置模型（需先把项目根加进 sys.path，避免 scripts/ 成为 sys.path[0]）。"""
    sys.path.insert(0, str(PROJECT_ROOT))
    from app.integrated_app.config_models import AppConfig

    return AppConfig


def _short_type(annotation: object) -> str:
    """把类型注解渲染成紧凑可读字符串。"""
    text = str(annotation).replace("typing.", "")
    # dict[str, app.integrated_app.config_models.ModelEntryConfig] → 只留类名
    text = text.split("(")[0] if text.startswith("<class") else text
    return text.replace("app.integrated_app.config_models.", "").replace("<class '", "").replace("'>", "")


def _short_value(value: object) -> str:
    """把默认值渲染成紧凑字符串，长列表/长串截断。"""
    if isinstance(value, str):
        text = f"'{value}'"
    else:
        text = repr(value)
    if len(text) > 70:
        text = text[:67] + "..."
    return text


def _rows_of(model_cls: type[BaseModel], prefix: str, values: dict) -> list[tuple[str, str, str]]:
    """展平一层字段为 (键路径, 类型, 默认值) 行；嵌套 BaseModel 递归展开。"""
    rows: list[tuple[str, str, str]] = []
    hints = get_type_hints(model_cls)
    for name, field in model_cls.model_fields.items():
        key = f"{prefix}{name}"
        annotation = hints.get(name, field.annotation)
        args = get_args(annotation)
        default = values.get(name) if isinstance(values, dict) else None
        if default is None and field.default is not None:
            default = field.default
        # 值为 Pydantic 模型（如 models: dict[str, ModelEntryConfig]）→ 展开其字段
        sub_models = [a for a in args if isinstance(a, type) and issubclass(a, BaseModel)]
        if sub_models:
            entry_cls = sub_models[0]
            sample = next(iter(default.values()), None) if isinstance(default, dict) else None
            if isinstance(sample, BaseModel):
                rows.extend(_rows_of(entry_cls, f"{key}.<size>.", sample.model_dump()))
                continue
            if not (isinstance(annotation, type) and issubclass(annotation, BaseModel)):
                # 默认空 dict（如 model.models）：用条目模型自身默认值展示字段模式
                try:
                    rows.extend(_rows_of(entry_cls, f"{key}.<size>.", entry_cls().model_dump()))
                    continue
                except Exception:
                    pass  # 条目模型有必填字段无法空构造 → 退回普通行展示类型
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            sub_dump = default.model_dump() if isinstance(default, BaseModel) else {}
            rows.extend(_rows_of(annotation, f"{key}.", sub_dump))
            continue
        rows.append((key, _short_type(annotation), _short_value(default)))
    return rows


def _section_doc(model_cls: type[BaseModel]) -> str:
    """取段类 docstring 的首句作为段落说明。"""
    doc = (model_cls.__doc__ or "").strip()
    if not doc:
        return ""
    first_line = doc.splitlines()[0].strip()
    return first_line.rstrip("。")


def generate() -> str:
    """生成完整 markdown 文本。"""
    app_config_cls = _load_app_config_model()
    try:
        config = app_config_cls()
    except Exception:
        # 存在必填字段的模型：回退到真实加载路径（读 config.yaml + 环境默认）
        sys.path.insert(0, str(PROJECT_ROOT))
        from app.integrated_app.config import get_app_config

        config = get_app_config()

    lines = [
        "---",
        "outline: [2, 3]",
        "---",
        "# 配置参考（自动生成）",
        "",
        "> 本页由 `scripts/generate_config_reference.py` 从 `app/integrated_app/config_models.py`"
        "（Pydantic 模型，含校验逻辑）自动生成，**请勿手改**；",
        "> 重新生成：`python scripts/generate_config_reference.py`。"
        "实际生效值以仓库根目录 `config.yaml` 为准（其中 `security` 段与密钥类配置见"
        " [安全与合规](/guide/security)）。",
        "",
    ]
    for name, field in app_config_cls.model_fields.items():
        annotation = field.annotation
        if not (isinstance(annotation, type) and issubclass(annotation, BaseModel)):
            lines.append(f"- `{name}` = `{_short_value(getattr(config, name, None))}`")
            continue
        section_cls = annotation
        doc = _section_doc(section_cls)
        lines.append(f"## `{name}` 段")
        if doc:
            lines.append(f"{doc}")
        lines.append("")
        lines.append("| 键 | 类型 | 默认值 |")
        lines.append("|---|---|---|")
        section_value = getattr(config, name, None)
        dump = section_value.model_dump() if isinstance(section_value, BaseModel) else {}
        for key, typ, default in _rows_of(section_cls, f"{name}.", dump):
            lines.append(f"| `{key}` | `{typ}` | `{default}` |")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成配置参考文档（website/docs/guide/config.md）")
    parser.add_argument("--check", action="store_true", help="仅校验文件是否为最新（过期则退出码 1）")
    args = parser.parse_args(argv)

    content = generate()
    if args.check:
        if not OUTPUT_PATH.exists() or OUTPUT_PATH.read_text(encoding="utf-8") != content:
            print(f"[FAIL] {OUTPUT_PATH} 过期或不存在 — 运行 python scripts/generate_config_reference.py 重新生成")
            return 1
        print("[PASS] config.md 为最新")
        return 0
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(content, encoding="utf-8", newline="\n")
    print(f"[OK] 已生成 {OUTPUT_PATH}（{len(content.splitlines())} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
