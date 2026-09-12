"""
roadmap.py 批量验证与状态更新脚本
对每项 FRAMEWORK_DONE 检查对应模块的实际实现深度，自动更新为 COMPLETED
"""

import ast
import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
ROADMAP_PATH = PROJECT_ROOT / "app" / "integrated_app" / "optimization" / "roadmap.py"
OPTIMIZATION_DIR = PROJECT_ROOT / "app" / "integrated_app" / "optimization"
INTEGRATED_DIR = PROJECT_ROOT / "app" / "integrated_app"

# 关键词映射：roadmap 功能项 → 代码中应出现的技术关键词
KEYWORD_MAP = {
    # VAE / Tiling
    "VAE Tiled 增强": ["groupnorm", "gaussian", "tile_size", "get_recommend"],
    "细粒度 Tiled 推理": ["make_tiled_fn", "tiled_encode", "tiled_decode"],
    "VAE Slicing/Tiling 优化": ["tiled", "slice", "vae"],
    "CPU Offload 机制": ["offload", "cpu", "cache"],
    "条件 VAE 解码": ["conditional", "condition", "decode"],
    "Tiled Chunked Decode": ["chunked", "sliding_window", "overlap"],
    "8bit 缓存量化": ["8bit", "int8", "quantize", "cache"],
    "Selective Block Offloading": ["selective", "block_importance", "offload"],
    "TeaCache 时间步跳过": ["teacache", "time_step", "skip", "cache"],
    # Temporal
    "Temporal Texture Guidance": ["temporal_texture", "ttg", "warp", "guidance"],
    "Stream Forward KV Cache": ["kv_cache", "stream", "forward"],
    "特征传播模块": ["propagation", "feature", "warp"],
    "光流引导可变形对齐": ["deformable", "flow", "align"],
    "Patch-level KV Cache": ["patch", "kv_cache", "causal"],
    "截断因果历史模型": ["truncated", "causal", "history"],
    "双向采样策略": ["bidirectional", "reverse", "forward"],
    "Second-order Grid Propagation": ["second_order", "grid", "propagation"],
    "ARTG 光流对齐": ["artg", "flow", "align"],
    "Temporal Processor Module": ["temporal_processor", "processor"],
    "递归-并行混合架构": ["recurrent", "parallel", "hybrid"],
    # Engine / Scheduler
    "多引擎调度框架": ["scheduler", "engine", "registry", "dispatch"],
    "Upscaler 抽象体系": ["upscaler", "abstract", "base"],
    "引擎兼容性检测": ["compatibility", "detect", "check"],
    "多后端 Processor 工厂模式": ["processor", "factory", "create"],
    "Registry 模式": ["registry", "register", "build"],
    "Pipeline 继承体系": ["pipeline", "inherit", "base"],
    "多 GPU 多线程调度": ["multi_gpu", "thread", "parallel"],
    "子进程引擎调用": ["subprocess", "spawn", "process"],
    # Specialized engines
    "CPU/轻量级引擎": ["cpu", "lightweight", "anime4k"],
    "DiffBIR 图像修复引擎": ["diffbir", "restore", "inpaint"],
    "人脸修复引擎": ["codeformer", "face", "gfpgan"],
    "动漫专用引擎": ["realcugan", "anime", "waifu2x"],
    "着色引擎": ["deoldify", "colorize", "color"],
    "压缩视频专用引擎": ["compressed", "ftvsr", "artifact"],
    "Video Inpainting 引擎": ["inpainting", "propainter", "remove"],
    # Post processing
    "小波重建后处理": ["wavelet", "reconstruct", "dwt"],
    "SRVGGNetCompact 后处理": ["srvgg", "compact", "esrgan", "sharpen"],
    "Alpha 通道处理": ["alpha", "transparency", "rgba"],
    "EXIF 元数据复制": ["exif", "metadata", "piexif"],
    "文本修复流水线": ["text", "ocr", "inpaint"],
    "Fidelity Weight 控制": ["fidelity", "weight", "adain"],
    "多步放大策略": ["multi_step", "progressive", "upscale"],
    # Diffusion sampling
    "One-step Distillation": ["one_step", "distill", "dapt"],
    "四步蒸馏推理": ["four_step", "distill", "stream"],
    "DPM-Solver++ 2M SDE": ["dpm", "solver", "sde"],
    "Noise Inversion": ["noise_inversion", "inverse", "ode"],
    "Dynamic CFG": ["dynamic_cfg", "cfg_scale", "adaptive"],
    "线性 CFG 策略": ["linear_cfg", "cfg_schedule"],
    "guide_rescale": ["guide_rescale", "rescale"],
    "多采样器统一接口": ["sampler", "unified", "interface"],
    "Flow Matching 调度器": ["flow_matching", "rectified_flow", "sigma"],
    # VRAM / GPU
    "VRAMPeakMonitor": ["peak", "monitor", "vram"],
    "FP8 量化 (torchao)": ["fp8", "torchao", "float8", "quantize"],
    "xformers 内存高效注意力": ["xformers", "memory_efficient", "attention"],
    "GPU 枚举兼容性检测": ["gpu", "enumerate", "compatibility"],
    "TensorRT 加速": ["tensorrt", "trt", "compile"],
    "torch.compile 集成": ["torch.compile", "compile", "dynamo"],
    "Gradient Checkpointing": ["gradient_checkpoint", "checkpoint", "activation"],
    "多后端自动检测": ["detect", "backend", "vulkan", "mps"],
    "Vulkan 跨GPU厂商": ["vulkan", "cross_platform"],
    "MPS/多设备支持": ["mps", "metal", "device"],
    "RTX VSR 硬件加速": ["rtx", "vsr", "hardware", "nvenc"],
    # Framework
    "YAML 配置驱动": ["yaml", "config", "load"],
    "配置驱动模型实例化": ["config", "instantiate", "build_model"],
    "自动检查点恢复": ["checkpoint", "resume", "restore"],
    "CPU/CUDA Prefetcher": ["prefetcher", "prefetch", "dataloader"],
    "模型自描述属性": ["self_describe", "metadata", "model_info"],
    "Python 绑定直调": ["pybind", "binding", "native"],
    "多 GPU 并行推理": ["data_parallel", "multi_gpu", "distributed"],
    "Hydra 配置管理": ["hydra", "omegaconf"],
    # Video
    "帧插值能力集成": ["interpolate", "rife", "frame"],
    "RAFT 光流集成": ["raft", "flow", "optical"],
    "视频帧分析": ["frame_analyze", "scene", "detect"],
    "RIFE 插帧集成": ["rife", "interpolate"],
    "分级退化处理": ["degradation", "light", "heavy", "adaptive"],
    "深度感知帧插值": ["depth", "dain", "interpolate"],
    "因果条件推理": ["causal", "condition", "streaming"],
    # DiT optimization
    "LCSA 稀疏注意力": ["lcsa", "sparse", "local_constraint"],
    "N维RoPE位置编码": ["rope", "pos_emb", "n_dim"],
    "ControlNet 条件注入": ["controlnet", "condition", "inject"],
    "双流 DiT 架构": ["dual_stream", "two_stream", "dit"],
    "频域注意力": ["frequency", "dct", "spectral", "attn"],
    "Mamba 时序建模": ["mamba", "ssm", "state_space"],
    "Codebook Lookup+Transformer": ["codebook", "lookup", "transformer"],
    "多模态融合架构": ["multimodal", "fusion", "event"],
    # WebUI
    "Gradio WebUI 设计参考": ["gradio", "ui", "interface"],
    "文件列表管理+进度报告": ["file_list", "progress", "queue"],
    "参数面板优化": ["parameter", "panel", "slider"],
    "Accordion 分组设计": ["accordion", "group", "collapse"],
    "设置持久化": ["persist", "settings", "save"],
    "文件拖拽支持": ["drag", "drop", "upload"],
}


def find_module_file(module_name: str) -> Path | None:
    """在 optimization 和 integrated_app 目录下查找模块文件"""
    # module_name 可能是 "vae_tiled_enhance.py" 或 "blockswap.py + vram_monitor.py"
    names = [n.strip() for n in re.split(r"[+]", module_name)]
    for name in names:
        name = name.strip()
        if not name:
            continue
        # 搜索 optimization 目录
        for root, _dirs, files in os.walk(OPTIMIZATION_DIR):
            if name in files:
                return Path(root) / name
        # 搜索 integrated_app 根目录
        p = INTEGRATED_DIR / name
        if p.exists():
            return p
        # 搜索 engines 子目录
        for root, _dirs, files in os.walk(INTEGRATED_DIR / "engines"):
            if name in files:
                return Path(root) / name
    return None


def analyze_file(filepath: Path) -> dict:
    """分析文件的实现深度"""
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception:
        return {"exists": False, "lines": 0, "defs": 0, "placeholders": 0, "placeholder_ratio": 1.0}

    lines = content.count("\n") + 1
    defs = len(re.findall(r"^\s*(def |class )\w+", content, re.MULTILINE))
    placeholders = len(
        re.findall(r"^\s*(pass|TODO|NotImplementedError|raise NotImplementedError)", content, re.MULTILINE)
    )
    placeholder_ratio = placeholders / max(defs, 1)

    return {
        "exists": True,
        "lines": lines,
        "defs": defs,
        "placeholders": placeholders,
        "placeholder_ratio": placeholder_ratio,
    }


def check_keywords(suggestion: str, filepath: Path) -> tuple[list[str], list[str]]:
    """检查功能项关键词是否在代码中出现（区分代码 vs docstring）"""
    keywords = []
    for key, kws in KEYWORD_MAP.items():
        if key in suggestion:
            keywords.extend(kws)
            break

    if not keywords:
        return [], []

    try:
        content = filepath.read_text(encoding="utf-8")
        # 移除 docstring 和注释，只检查代码
        code_only = re.sub(r'"""[\s\S]*?"""', "", content)
        code_only = re.sub(r"'''[\s\S]*?'''", "", code_only)
        code_only = re.sub(r"#.*$", "", code_only, flags=re.MULTILINE)
    except Exception:
        return [], keywords

    hit = []
    miss = []
    for kw in keywords:
        if re.search(re.escape(kw), code_only, re.IGNORECASE):
            hit.append(kw)
        else:
            miss.append(kw)

    return hit, miss


def should_be_completed(item: dict, analysis: dict, hit: list[str], miss: list[str]) -> tuple[bool, str]:
    """判定是否应更新为 COMPLETED"""
    if not analysis["exists"]:
        return False, "文件不存在"

    if analysis["defs"] < 3:
        return False, f"函数过少({analysis['defs']})"

    if analysis["placeholder_ratio"] > 0.3:
        return False, f"占位符比例过高({analysis['placeholder_ratio']:.1%})"

    if analysis["lines"] < 100:
        return False, f"代码量过少({analysis['lines']}行)"

    # 如果有关键词映射，检查命中率
    if miss and len(miss) > len(hit):
        return False, f"关键词命中率低({len(hit)}/{len(hit)+len(miss)})"

    return True, f"已实现({analysis['lines']}行/{analysis['defs']}函数, 关键词命中{len(hit)})"


def main():
    # 解析 roadmap.py
    with open(ROADMAP_PATH, encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source)

    items = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and hasattr(node.func, "id") and node.func.id == "FeatureItem":
            args = node.args
            if len(args) >= 5:
                fid = args[0].value if isinstance(args[0], ast.Constant) else -1
                suggestion = args[1].value if isinstance(args[1], ast.Constant) else ""
                src = args[2].value if isinstance(args[2], ast.Constant) else ""
                module = args[3].value if isinstance(args[3], ast.Constant) else ""
                priority = args[4].attr if isinstance(args[4], ast.Attribute) else ""
                status = "FRAMEWORK_DONE"  # default
                if len(args) >= 6 and isinstance(args[5], ast.Attribute):
                    status = args[5].attr
                items.append(
                    {
                        "id": fid,
                        "suggestion": suggestion,
                        "source": src,
                        "module": module,
                        "priority": priority,
                        "status": status,
                        "lineno": node.lineno,
                        "end_lineno": node.end_lineno,
                    }
                )

    print(f"解析到 {len(items)} 项 FeatureItem")

    # 验证每项
    results = []
    to_update = []  # (item, reason)

    for item in items:
        if item["status"] != "FRAMEWORK_DONE":
            results.append({**item, "verdict": "skip", "reason": f"已是{item['status']}"})
            continue

        filepath = find_module_file(item["module"])
        if filepath:
            analysis = analyze_file(filepath)
            hit, miss = check_keywords(item["suggestion"], filepath)
            completed, reason = should_be_completed(item, analysis, hit, miss)
        else:
            analysis = {"exists": False, "lines": 0, "defs": 0, "placeholders": 0, "placeholder_ratio": 1.0}
            hit, miss = [], []
            completed, reason = False, "模块文件未找到"

        results.append(
            {
                **item,
                "file": str(filepath) if filepath else "NOT_FOUND",
                "analysis": analysis,
                "keyword_hit": hit,
                "keyword_miss": miss,
                "verdict": "COMPLETED" if completed else "KEEP_FRAMEWORK_DONE",
                "reason": reason,
            }
        )
        if completed:
            to_update.append((item, reason))

    # 统计
    fw_items = [r for r in results if r["status"] == "FRAMEWORK_DONE"]
    completed_count = len([r for r in fw_items if r["verdict"] == "COMPLETED"])
    keep_count = len([r for r in fw_items if r["verdict"] == "KEEP_FRAMEWORK_DONE"])

    print("\n=== 验证结果 ===")
    print(f"FRAMEWORK_DONE 项: {len(fw_items)}")
    print(f"  → 应更新为 COMPLETED: {completed_count}")
    print(f"  → 保持 FRAMEWORK_DONE: {keep_count}")

    print("\n=== 保持 FRAMEWORK_DONE 的项（需开发/确认）===")
    for r in fw_items:
        if r["verdict"] == "KEEP_FRAMEWORK_DONE":
            print(f"  [{r['priority']}] id={r['id']} | {r['suggestion'][:40]} | {r['reason']}")

    print("\n=== 将更新为 COMPLETED 的项（前20）===")
    for r in fw_items[:20]:
        if r["verdict"] == "COMPLETED":
            print(f"  [{r['priority']}] id={r['id']} | {r['suggestion'][:40]} | {r['reason']}")

    # 保存验证结果
    import json

    with open(PROJECT_ROOT / "roadmap_verification.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print("\n验证结果已保存到 roadmap_verification.json")

    return to_update


if __name__ == "__main__":
    main()
