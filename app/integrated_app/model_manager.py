#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2024-2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""SeedVR2 - 模型管理器模块

本模块实现模型生命周期管理，负责 SeedVR2 模型的加载、卸载、切换和显存预检，
是应用层与推理引擎之间的协调层。

所属项目: SeedVR2 - SeedVR2 视频修复独立应用
核心技术栈: Python 3.10+, asyncio, PyTorch CUDA

模块职责:
- 模型配置解析与验证（从 config.yaml 读取模型路径、大小、精度配置）
- 智能精度推荐（根据可用显存和实际可用模型自动选择 fp16/fp8/mxfp8/int8_convrot/nvfp4）
- 模型文件存在性检查与缺失时的精度回退
- 显存预检与加载前的资源验证
- 模型加载/卸载/切换的完整生命周期管理
- 模型切换失败时的自动回滚机制
- 与 model_registry 集成，同步全局模型状态

设计原则:
- 容错设计: 模型文件缺失或显存不足时自动尝试回退方案
- 安全切换: 切换模型前保存状态，失败时自动回滚到之前的模型
- 幂等操作: 重复加载相同模型直接返回成功，不重复加载
- 显存保护: 加载前严格检查显存，避免 OOM 导致系统不稳定
"""

import asyncio
import json
import logging
import os

import torch

from app.integrated_app.engine_interface import RestoreEngine
from app.integrated_app.engines.seedvr2_engine import SeedVR2Engine
from app.integrated_app.gpu_utils import check_vram_available, clear_gpu_cache, estimate_model_vram
from app.integrated_app.model_registry import model_registry
from app.integrated_app.utils.hashing import compute_file_sha256

logger = logging.getLogger(__name__)


class ModelManager:
    """SeedVR2 模型生命周期管理器

    管理模型的加载、卸载、切换，提供显存预检、精度推荐、文件验证等功能。
    通过 model_registry 维护全局模型状态，支持 SSE 状态推送。

    核心功能:
    - 智能精度推荐: 根据 GPU 显存和实际可用模型自动选择最优精度（fp16/fp8/mxfp8/int8_convrot/nvfp4）
    - 显存预检: 加载前估算显存需求，检查是否有足够资源
    - 自动回退: 请求的精度文件不存在时自动尝试另一种精度
    - 安全切换: 切换模型失败时自动回滚到之前的模型
    - 状态同步: 所有状态变更自动同步到 model_registry

    Attributes:
        config (dict): 应用配置字典（从 config.yaml 加载）
        model_config (dict): 模型配置子字典
    """

    def __init__(self, config: dict):
        """初始化模型管理器

        Args:
            config: 完整的应用配置字典，应包含 "model" 键下的模型配置，
                    包括模型路径、可用模型大小、默认参数等
        """
        self.config = config
        self.model_config = config.get("model", {})
        self._engine: RestoreEngine | None = None
        # P1-5：加载互斥锁——并发请求触发加载时串行化，第二个等待者拿到锁后
        # 走幂等短路，杜绝重复加载与后写者覆盖
        self._load_lock = asyncio.Lock()

    @property
    def is_loaded(self) -> bool:
        """检查模型是否已加载

        Returns:
            bool: 模型已加载并可用于推理返回 True，否则返回 False

        Note:
            状态直接从全局 model_registry 获取，保证线程安全
        """
        return model_registry.model_loaded

    @property
    def engine(self) -> RestoreEngine | None:
        """获取当前引擎实例

        Returns:
            RestoreEngine | None: 当前激活的引擎实例，未加载时为 None

        Note:
            引擎引用从全局 model_registry 获取，确保始终是最新实例
        """
        return model_registry.get_engine()

    def get_model_info(self, size: str) -> dict | None:
        """获取指定大小模型的配置信息

        Args:
            size: 模型大小标识，如 "3b"、"7b"

        Returns:
            dict | None: 模型配置字典，包含 checkpoint 路径、显存需求等信息；
                        未找到对应大小的模型时返回 None
        """
        return self.model_config.get("models", {}).get(size)

    def get_pretrained_dir(self) -> str:
        """获取预训练模型根目录的绝对路径

        根据 model_source_mode 配置解析模型根目录:
        - portable 模式（默认）: {project_root}/{pretrained_dir}
        - shared 模式: 使用 shared_models_root 指定的外部共享目录

        Returns:
            str: 预训练模型目录的绝对路径
        """
        from app.integrated_app.config_models import get_pretrained_root

        return get_pretrained_root(self.model_config)

    def check_model_exists(self, size: str, precision: str | None = None) -> bool:
        """检查指定模型文件是否存在

        验证指定大小和精度的模型 checkpoint 文件是否存在于文件系统中。

        Args:
            size: 模型大小标识 (如 "3b", "7b")
            precision: 模型精度 (如 "fp16", "fp8", "mxfp8", "int8_convrot", "nvfp4")，None 时使用配置中的默认精度

        Returns:
            bool: 模型文件存在返回 True，否则返回 False
        """
        model_info = self.get_model_info(size)
        if not model_info:
            return False

        pretrained_dir = self.get_pretrained_dir()
        if precision is None:
            precision = self.model_config.get("default_precision", "fp16")
        checkpoint_key = f"checkpoint_{precision}"
        checkpoint = model_info.get(checkpoint_key) or model_info.get("checkpoint_fp16", "")
        checkpoint_path = os.path.join(pretrained_dir, checkpoint)
        return os.path.exists(checkpoint_path)

    def get_recommended_precision(self, model_size: str) -> str:
        """根据 GPU 显存和实际可用模型推荐最佳精度。

        检测 CUDA 设备的总显存，先筛选用户实际拥有的模型精度（文件存在），
        再从中推荐：显存充足选 fp16，否则按 fp8 → mxfp8 → int8_convrot → nvfp4
        顺序选第一个可用的低显存精度。v1.5.1 起支持五精度，不再只返回 fp16/fp8。

        Args:
            model_size: 模型大小标识 (如 "3b", "7b")

        Returns:
            str: 推荐的精度标识

        Note:
            - 量化格式（mxfp8/int8_convrot/nvfp4）为加载期反量化，驻留显存≈fp16，
              但模型文件更小、加载更快，适合磁盘空间有限的场景
            - 即使显存不足也返回最低可用精度，由上层决定是否允许加载
        """
        model_info = self.get_model_info(model_size)
        if not model_info:
            return "fp16"

        min_fp16_gb = model_info.get("min_vram_fp16_gb", 16)

        try:
            if torch.cuda.is_available():
                total_vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            else:
                total_vram_gb = 0
        except Exception:
            total_vram_gb = 0

        # 筛选用户实际拥有的精度（文件存在）
        all_precisions = ["fp16", "fp8", "mxfp8", "int8_convrot", "nvfp4"]
        available = [p for p in all_precisions if self.check_model_exists(model_size, p)]

        # 无任何模型文件时（如测试环境、用户未下载权重），回退到纯显存推荐逻辑
        if not available:
            if total_vram_gb >= min_fp16_gb:
                return "fp16"
            min_fp8_gb = model_info.get("min_vram_fp8_gb", 8)
            if total_vram_gb < min_fp8_gb:
                logger.warning(
                    f"显存 {total_vram_gb:.1f}GB 不足以运行 {model_size} 模型 (最低需要 {min_fp8_gb}GB)，推荐使用 FP8 精度"
                )
            return "fp8"

        # 显存充足且拥有 fp16 → 推荐 fp16（质量最优）
        if "fp16" in available and total_vram_gb >= min_fp16_gb:
            return "fp16"

        # 显存不足或无 fp16 → 从低显存精度中按顺序选第一个可用的
        low_vram_order = ["fp8", "mxfp8", "int8_convrot", "nvfp4"]
        for p in low_vram_order:
            if p in available:
                return p

        # fallback：返回第一个可用精度
        logger.warning(f"显存 {total_vram_gb:.1f}GB，可用精度 {available}，推荐 {available[0]}")
        return available[0]

    # ==================== 权重完整性校验（数据治理 P1-3） ====================

    _HASH_CACHE_REL_PATH = os.path.join("data", "model_hash_cache.json")

    def _load_hash_cache(self, cache_path: str) -> dict:
        """读取权重哈希缓存（JSON：绝对路径 → {size, mtime, sha256}）。"""
        try:
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _sha256_with_cache(self, path: str, cache_path: str, cache: dict) -> str:
        """带缓存计算文件 sha256（size+mtime 命中免重算，GB 级权重二次加载近零开销）。

        Args:
            path: 权重文件路径。
            cache_path: 缓存 JSON 落盘路径。
            cache: 进程内缓存字典（会被原地更新并落盘）。

        Returns:
            hex 摘要；文件不可读时返回空串。
        """
        try:
            stat = os.stat(path)
        except OSError:
            return ""
        key = os.path.normcase(os.path.abspath(path))
        entry = cache.get(key)
        if isinstance(entry, dict) and entry.get("size") == stat.st_size and entry.get("mtime") == stat.st_mtime:
            digest = entry.get("sha256")
            if isinstance(digest, str) and len(digest) == 64:
                return digest
        digest = compute_file_sha256(path)
        cache[key] = {"size": stat.st_size, "mtime": stat.st_mtime, "sha256": digest}
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            tmp_path = cache_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(cache, f)
            os.replace(tmp_path, cache_path)
        except OSError as e:
            logger.debug(f"权重哈希缓存写盘失败（不影响校验）: {e}")
        return digest

    async def verify_weight_hashes(self, model_size: str, precision: str) -> None:
        """加载前权重 sha256 白名单校验（数据治理 P1-3）。

        手动放置的任意权重文件此前「文件存在即加载」，构成恶意权重加载面。
        本方法对即将加载的权重文件（checkpoint / vae / pos_emb / neg_emb）逐一比对
        config.yaml 中配置的期望哈希：

        - 已配置哈希且不匹配 → 拒绝加载（ValueError）
        - 已配置哈希且匹配 → 放行（info 日志）
        - 未配置期望哈希 → 告警放行（无法校验未知配置，兼容自定义权重场景）
        - 文件不存在 → 跳过（存在性由 check_model_exists/引擎负责）

        Args:
            model_size: 模型大小标识（如 "3b"）。
            precision: 已解析的最终精度（回退决策之后）。

        Raises:
            ValueError: 任一已配置哈希的权重文件校验不通过。
        """
        model_cfg = self.get_model_info(model_size)
        if not model_cfg:
            return
        pretrained_dir = self.get_pretrained_dir()
        candidates = [
            (model_cfg.get(f"checkpoint_{precision}") or "", model_cfg.get(f"sha256_{precision}") or "", "checkpoint"),
            (model_cfg.get("vae_checkpoint") or "", model_cfg.get("sha256_vae") or "", "vae"),
            (model_cfg.get("pos_emb") or "", model_cfg.get("sha256_pos_emb") or "", "pos_emb"),
            (model_cfg.get("neg_emb") or "", model_cfg.get("sha256_neg_emb") or "", "neg_emb"),
        ]
        cache_path = os.path.join(os.getcwd(), self._HASH_CACHE_REL_PATH)
        cache = self._load_hash_cache(cache_path)
        for filename, expected, label in candidates:
            if not filename:
                continue
            path = os.path.join(pretrained_dir, filename)
            if not os.path.exists(path):
                continue
            if not expected:
                logger.warning(f"权重文件未配置期望哈希，跳过白名单校验: {filename}")
                continue
            digest = await asyncio.to_thread(self._sha256_with_cache, path, cache_path, cache)
            if not digest or digest.lower() != expected.lower():
                raise ValueError(
                    f"权重文件 SHA256 校验失败: {filename}（{label}）。"
                    f"文件可能损坏或被替换，请重新下载（python scripts/download_model.py）"
                    f"或删除 {path} 后重试。"
                )
            logger.info(f"权重校验通过: {filename}（{label}）")

    async def load_model(
        self, model_size: str | None = None, device: str | None = None, precision: str | None = None
    ) -> dict:
        """加载指定模型到 GPU

        完整的模型加载流程，包含参数验证、GPU 检查、文件检查、显存预检、
        精度回退、引擎创建、状态同步等步骤。

        Args:
            model_size: 模型大小 (如 "3b", "7b")，None 时使用配置中的 default_size
            device: 推理设备 ("auto"/"cuda")，None 时使用配置中的 device
            precision: 模型精度 ("fp16"/"fp8"/"mxfp8"/"int8_convrot"/"nvfp4"/"auto")，None 或 "auto" 时根据显存和可用模型自动选择

        Returns:
            dict: 加载结果字典，包含:
                - status: "ok" 表示成功
                - message: 人类可读的状态消息
                - model_size: 实际加载的模型大小
                - precision: 实际使用的精度
                - device: 使用的设备

        Raises:
            RuntimeError: 未检测到 NVIDIA GPU（SeedVR2 仅支持 CUDA）
            ValueError: 指定了未知的模型大小
            FileNotFoundError: 模型 checkpoint 文件不存在（所有已配置精度均无对应文件）
            MemoryError: 显存不足无法加载模型（即使尝试 fp8 回退也不足）
        """
        if model_size is None:
            model_size = self.model_config.get("default_size", "3b")
        if device is None:
            device = self.model_config.get("device", "auto")
        if precision is None:
            precision = self.model_config.get("default_precision", "fp16")
        if precision == "auto":
            precision = self.get_recommended_precision(model_size)

        # Check if the same model is already loaded BEFORE the GPU check.
        # This allows returning the "already loaded" short-circuit even in
        # CPU-only / no-GPU environments (e.g. CI, unit tests), avoiding a
        # spurious RuntimeError when the model is already in memory.
        if (
            model_registry.model_loaded
            and model_registry.current_model_size == model_size
            and model_registry.current_precision == precision
        ):
            logger.info(f"模型 {model_size}/{precision} 已加载，跳过")
            return {
                "status": "ok",
                "message": f"模型 {model_size}/{precision} 已加载",
                "model_size": model_size,
                "precision": precision,
            }

        # P1-5：持锁加载（锁内二次幂等检查，见 _load_model_locked）
        async with self._load_lock:
            return await self._load_model_locked(model_size=model_size, device=device, precision=precision)

    async def _load_model_locked(self, model_size: str, device: str, precision: str) -> dict:
        """执行实际加载（必须持有 self._load_lock 调用）。"""
        # 锁内二次幂等检查：并发场景下第一个等待者进入时模型可能已被前者加载
        if (
            model_registry.model_loaded
            and model_registry.current_model_size == model_size
            and model_registry.current_precision == precision
        ):
            logger.info(f"模型 {model_size}/{precision} 已加载（锁内短路），跳过")
            return {
                "status": "ok",
                "message": f"模型 {model_size}/{precision} 已加载",
                "model_size": model_size,
                "precision": precision,
            }

        from app.integrated_app.gpu_backend import gpu_manager

        if not gpu_manager.is_gpu_available:
            raise RuntimeError(
                "SeedVR2 仅支持 NVIDIA GPU 推理，当前未检测到 NVIDIA GPU。"
                "请安装 NVIDIA GPU 并配置 CUDA 驱动以启用推理功能。"
            )

        model_cfg = self.get_model_info(model_size)
        if not model_cfg:
            raise ValueError(f"未知的模型大小: {model_size}")

        if not self.check_model_exists(model_size, precision):
            # 第一回退：fp16↔fp8 互备（numz 源，历史默认路径）
            fallback_precision = "fp8" if precision == "fp16" else "fp16"
            if self.check_model_exists(model_size, fallback_precision):
                logger.warning(f"{precision} 模型文件不存在，回退到 {fallback_precision}")
                precision = fallback_precision
            else:
                # 第二回退：遍历所有已配置精度（含 Comfy-Org 量化格式 mxfp8/int8_convrot/nvfp4），
                # 找到第一个文件存在的精度。v1.5.1 起五精度并存，用户可能只下载了其中一种。
                all_precisions = ["fp16", "fp8", "mxfp8", "int8_convrot", "nvfp4"]
                tried = [precision, fallback_precision]
                found = None
                for p in all_precisions:
                    if p in tried:
                        continue
                    if model_cfg.get(f"checkpoint_{p}") and self.check_model_exists(model_size, p):
                        found = p
                        break
                if found:
                    logger.warning(f"{precision}/{fallback_precision} 均不存在，回退到可用精度 {found}")
                    precision = found
                else:
                    configured = [p for p in all_precisions if model_cfg.get(f"checkpoint_{p}")]
                    raise FileNotFoundError(
                        f"模型文件不存在: 已尝试 {', '.join(tried)}，"
                        f"已配置精度 {', '.join(configured) if configured else '无'} 均无对应文件。"
                        f"请下载模型权重到 {self.get_pretrained_dir()}/"
                    )

        # 数据治理 P1-3：加载前权重 sha256 白名单校验
        # （手动放置的任意权重不再「文件存在即加载」；校验失败拒绝加载）
        await self.verify_weight_hashes(model_size, precision)

        required_vram = estimate_model_vram(model_size, precision=precision)
        can_load, available_vram = check_vram_available(required_vram)
        if not can_load:
            logger.warning(f"显存不足: 需要 {required_vram}MB，可用 {available_vram}MB")
            if device == "auto" and precision == "fp16":
                fp8_vram = estimate_model_vram(model_size, precision="fp8")
                can_load_fp8, available_fp8 = check_vram_available(fp8_vram)
                if can_load_fp8 and self.check_model_exists(model_size, "fp8"):
                    logger.warning("尝试切换到 FP8 精度以减少显存需求")
                    precision = "fp8"
                else:
                    raise MemoryError(
                        f"显存不足: 需要 {required_vram}MB，可用 {available_vram}MB。"
                        f"SeedVR2 仅支持 NVIDIA GPU 推理，不支持 CPU。"
                    )
            else:
                raise MemoryError(
                    f"显存不足: 需要 {required_vram}MB，可用 {available_vram}MB。"
                    f"SeedVR2 仅支持 NVIDIA GPU 推理，不支持 CPU。"
                )

        logger.info(f"正在加载模型: {model_cfg.get('name', model_size)}/{precision}, 设备: {device}")

        # P1-5：加载中状态经 model_status 事件广播（SSE 客户端可感知）
        model_registry.set_load_in_progress(True)
        try:
            engine = SeedVR2Engine(self.config)
            await engine.load_model(model_size=model_size, device=device, precision=precision)

            model_registry.set_engine(engine)
            self._engine = engine  # type: ignore[assignment]
        finally:
            model_registry.set_load_in_progress(False)

        logger.info(f"模型加载完成: {model_size}/{precision}")
        return {
            "status": "ok",
            "message": f"模型 {model_size}/{precision} 加载成功",
            "model_size": model_size,
            "precision": precision,
            "device": device,
        }

    async def unload_model(self) -> dict:
        """卸载当前模型并释放所有 GPU/CPU 资源

        执行完整的卸载流程: 调用引擎卸载方法、清除 registry 中的引擎引用、
        清空 GPU 缓存。

        Returns:
            dict: 卸载结果字典，包含 status 和 message 字段

        Note:
            - 如果没有已加载的模型，直接返回成功，不报错
            - 卸载后 model_registry 状态会被重置为未加载
            - 会触发 GPU 缓存清理，尽力释放显存
        """
        if not model_registry.model_loaded:
            logger.info("没有已加载的模型")
            return {"status": "ok", "message": "没有已加载的模型"}

        engine = model_registry.get_engine()
        if engine is not None:
            logger.info(f"正在卸载模型: {model_registry.current_model_size}")
            await engine.unload_model()

        model_registry.clear_engine()
        self._engine = None
        clear_gpu_cache()

        logger.info("模型已卸载，显存已释放")
        return {"status": "ok", "message": "模型已卸载"}

    async def switch_model(self, model_size: str, device: str | None = None, precision: str | None = None) -> dict:
        """安全切换模型（先卸载旧模型，再加载新模型，失败则回滚）

        切换流程:
        1. 如果目标模型已加载，直接返回成功
        2. 保存当前模型状态用于回滚
        3. 卸载当前模型（如果已加载）
        4. 尝试加载新模型
        5. 如果加载失败，尝试回滚到之前的模型
        6. 如果回滚也失败，清除引擎状态

        Args:
            model_size: 目标模型大小
            device: 目标设备，None 时使用默认值
            precision: 目标精度，None 时自动推荐

        Returns:
            dict: 切换结果字典，格式同 load_model() 返回值

        Raises:
            RuntimeError: 切换失败且回滚也失败时抛出，包含原始错误信息

        Note:
            - 回滚机制确保切换失败时系统不会处于无模型可用的状态
            - 如果回滚时重新加载之前的模型也失败，会清除引擎状态并记录错误
        """
        if (
            model_registry.current_model_size == model_size
            and model_registry.model_loaded
            and (precision is None or model_registry.current_precision == precision)
        ):
            return {"status": "ok", "message": f"模型 {model_size} 已加载", "model_size": model_size}

        previous_size = model_registry.current_model_size
        previous_precision = model_registry.current_precision
        model_registry.get_engine()
        previous_loaded = model_registry.model_loaded

        if previous_loaded:
            await self.unload_model()

        try:
            result = await self.load_model(model_size=model_size, device=device, precision=precision)
            return result
        except Exception as e:
            logger.error(f"切换模型失败: {e}")

            if previous_loaded and previous_size is not None:
                logger.info(f"正在回滚到之前的模型: {previous_size}")
                try:
                    await self.load_model(model_size=previous_size, precision=previous_precision)
                    logger.info(f"已回滚到模型: {previous_size}")
                except Exception as rollback_err:
                    logger.error(f"回滚失败: {rollback_err}")
                    model_registry.clear_engine()

            raise RuntimeError(f"切换模型失败: {e}，已回滚到之前的模型") from e

    def get_current_model_info(self) -> dict:
        """获取当前已加载模型的状态信息

        Returns:
            dict: 模型状态字典，直接从 model_registry.get_status() 获取，包含:
                - model_loaded: bool - 是否已加载
                - current_model_size: str | None - 当前模型大小
                - current_precision: str | None - 当前精度
                - model_info: dict - 模型详细信息
        """
        return model_registry.get_status()

    def get_status(self) -> dict:
        """获取模型管理器的完整状态

        在 model_registry 状态基础上增加可用模型列表。

        Returns:
            dict: 完整状态字典，包含 model_registry 的所有字段，
                 以及 "available_models" 键列出所有支持的模型大小
        """
        status = model_registry.get_status()
        status["available_models"] = list(self.model_config.get("models", {}).keys())
        return status
