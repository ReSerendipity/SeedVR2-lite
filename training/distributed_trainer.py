"""分布式训练支持 — DeepSpeed + FSDP + DDP。

基于项目已有的 ``common.distributed`` 基础设施，提供高层训练器封装，
支持多种并行策略和断点续训。

支持的并行策略:
    - **DDP (DistributedDataParallel)**: 数据并行，每卡持有完整模型副本
    - **FSDP (Fully Sharded Data Parallel)**: 模型分片，参数/梯度/优化器状态跨卡分片
    - **Hybrid Sharding**: 节点内分片 + 节点间数据并行

启动方式::

    # 4 卡 DDP
    torchrun --nproc_per_node=4 training/distributed_trainer.py --config config.yaml

    # 单卡调试
    python training/distributed_trainer.py --config config.yaml

依赖:
    - torch >= 2.4.0
    - common.distributed (项目内置)

验收标准:
    - 4 卡并行加速比 ≥ 3.5x
    - 支持 ZeRO 优化（显存节省 60%）
    - 断点续训正常
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import torch
import torch.nn as nn

# 项目内置分布式工具
from common.distributed import convert_to_ddp, get_device, get_global_rank, get_local_rank, get_world_size, init_torch
from common.seed import set_seed

logger = logging.getLogger(__name__)

# 裁剪前梯度总范数超过 max_gradient_norm 的该倍数即视为尖峰并告警
_GRAD_NORM_SPIKE_FACTOR = 10

# 按步保存的检查点文件名模式（滚动清理只针对无后缀的纯按步快照，
# epoch 结尾快照 `checkpoint_step_N_epoch_M.pt` 不在清理范围）
_STEP_CHECKPOINT_PATTERN = re.compile(r"^checkpoint_step_(\d+)\.pt$")


@dataclass
class TrainingConfig:
    """分布式训练配置。

    Attributes:
        batch_size: 全局批次大小（会被 world_size 等分）。
        learning_rate: 学习率。
        weight_decay: 权重衰减。
        epochs: 训练轮数。
        warmup_steps: 学习率预热步数。
        gradient_accumulation_steps: 梯度累积步数（模拟更大批次）。
        max_gradient_norm: 梯度裁剪阈值（None 表示不裁剪）。
        sharding_strategy: 分片策略（"ddp", "fsdp", "hybrid"）。
        checkpoint_dir: 检查点保存目录。
        checkpoint_interval: 检查点保存间隔（步数）。
        keep_last_checkpoints: 仅保留最近 N 个按步保存的检查点，防止磁盘被全量权重撑爆
            （0 表示不清理，保留全部）。
        resume_from_checkpoint: 断点续训的检查点路径。
        log_interval: 日志打印间隔（步数）。
        seed: 基础随机种子（None 表示不播种）。分布式下各 rank 自动按 rank 偏移，
            保证数据增强/dropout 各卡不同且跨次运行可复现。
        enable_experiment_tracking: 是否启用实验追踪（rank 0 落盘 JSONL，
            wandb 可用时可选上传）。
        experiment_name: 实验名（缺省按时间戳生成）。
        experiment_project: wandb 项目名。
        experiment_use_wandb: 是否尝试上传 wandb（False 时纯本地 JSONL）。
        heartbeat_interval: 心跳落盘间隔（步数），0 表示禁用。每次心跳把
            {step, epoch, timestamp} 原子写入 heartbeat_dir，供外部看门狗
            检测"训练静默死亡"。
        heartbeat_dir: 心跳文件目录。
        mixed_precision: 混合精度类型（"fp16", "bf16", None）。
        gradient_checkpointing: 是否启用梯度检查点（节省显存）。
    """

    batch_size: int = 32
    learning_rate: float = 1e-4
    weight_decay: float = 0.01
    epochs: int = 100
    warmup_steps: int = 500
    gradient_accumulation_steps: int = 1
    max_gradient_norm: float | None = 1.0
    sharding_strategy: str = "ddp"
    checkpoint_dir: str = "data/checkpoints"
    checkpoint_interval: int = 500
    keep_last_checkpoints: int = 3
    resume_from_checkpoint: str | None = None
    log_interval: int = 10
    seed: int | None = None
    enable_experiment_tracking: bool = False
    experiment_name: str | None = None
    experiment_project: str = "seedvr2-training"
    experiment_use_wandb: bool = False
    heartbeat_interval: int = 50
    heartbeat_dir: str = "data/heartbeats"
    mixed_precision: str | None = None
    gradient_checkpointing: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def _seed_worker(worker_id: int) -> None:
    """DataLoader worker 种子初始化。

    PyTorch 会基于 base_seed 为每个 worker 派生独立种子（base_seed 由主进程
    torch RNG 决定，因而受 ``set_seed`` 控制），但 ``random``/``numpy`` 全局
    状态不会自动同步，这里补齐，保证多 worker 数据增强可复现。

    Args:
        worker_id: worker 进程编号。
    """
    import numpy as np

    worker_seed = torch.initial_seed() % 2**32
    np.random.seed(worker_seed)
    random.seed(worker_seed)


class DistributedTrainer:
    """多 GPU 分布式训练器。

    封装分布式训练的完整生命周期：初始化 → 模型设置 → 数据加载 →
    训练循环 → 检查点保存/恢复 → 清理。

    支持三种分片策略:
        - ``"ddp"``: DistributedDataParallel（最简单，每卡完整模型）
        - ``"fsdp"``: Fully Sharded Data Parallel（参数跨卡分片）
        - ``"hybrid"``: 混合分片（节点内 FSDP + 节点间 DDP）

    Args:
        config: 训练配置。

    Example::

        config = TrainingConfig(sharding_strategy="ddp", epochs=100)
        trainer = DistributedTrainer(config)
        trainer.setup_model(model)
        trainer.setup_dataloader(dataset)
        trainer.train(model, dataloader, optimizer, compute_loss_fn)
        trainer.cleanup()
    """

    def __init__(self, config: TrainingConfig) -> None:
        self.config = config
        self.local_rank = get_local_rank()
        self.world_size = get_world_size()
        self.global_rank = get_global_rank()
        self.device = get_device()
        self._step = 0
        self._epoch = 0
        self._last_heartbeat_step = -1

        # 初始化分布式环境
        if self.world_size > 1:
            init_torch()
            logger.info(
                "分布式训练已初始化: rank=%d, world_size=%d, device=%s",
                self.global_rank,
                self.world_size,
                self.device,
            )
        else:
            logger.info("单卡模式训练, device=%s", self.device)

        # 全 RNG（python/numpy/torch CPU+CUDA）统一播种；分布式下按 rank 偏移
        if config.seed is not None:
            set_seed(config.seed)
            logger.info(
                "随机种子已设置: base=%d, rank=%d",
                config.seed,
                self.global_rank,
            )

        # 实验追踪（仅 rank 0 落盘/上传；wandb 缺失或初始化失败时自动降级本地 JSONL）
        self._tracker: Any | None = None
        if config.enable_experiment_tracking and self.global_rank == 0:
            try:
                from app.utils.experiment_tracker import ExperimentTracker

                self._tracker = ExperimentTracker(
                    project_name=config.experiment_project,
                    experiment_name=config.experiment_name,
                    config=vars(config),
                    use_wandb=config.experiment_use_wandb,
                )
                self._tracker.log_hyperparameters(vars(config))
            except Exception as e:
                logger.warning("实验追踪器初始化失败，本次训练不追踪: %s", e)
                self._tracker = None

        # 混合精度设置
        self._scaler: torch.cuda.amp.GradScaler | None = None
        if config.mixed_precision == "fp16":
            self._scaler = torch.cuda.amp.GradScaler()
        self._autocast_dtype = {
            "fp16": torch.float16,
            "bf16": torch.bfloat16,
            None: None,
        }.get(config.mixed_precision)

    def setup_model(self, model: nn.Module) -> nn.Module:
        """设置分布式模型。

        根据配置的分片策略，将模型移动到正确设备并包装为分布式模型。

        Args:
            model: 待训练的模型。

        Returns:
            包装后的分布式模型（DDP/FSDP 或原始模型）。
        """
        model = model.to(self.device)

        # 梯度检查点
        if self.config.gradient_checkpointing and hasattr(model, "gradient_checkpointing_enable"):
            model.gradient_checkpointing_enable()
            logger.info("已启用梯度检查点")

        if self.world_size > 1:
            if self.config.sharding_strategy == "ddp":
                model = convert_to_ddp(model)
                logger.info("模型已使用 DDP 包装")
            elif self.config.sharding_strategy in ("fsdp", "hybrid"):
                model = self._wrap_fsdp(model)
                logger.info("模型已使用 FSDP 包装 (strategy=%s)", self.config.sharding_strategy)
            else:
                logger.warning(
                    "未知分片策略 '%s'，回退到 DDP",
                    self.config.sharding_strategy,
                )
                model = convert_to_ddp(model)

        return model

    def _wrap_fsdp(self, model: nn.Module) -> nn.Module:
        """使用 FSDP 包装模型。

        Args:
            model: 待包装的模型。

        Returns:
            FSDP 包装后的模型。
        """
        from torch.distributed.fsdp import FullyShardedDataParallel as FSDP
        from torch.distributed.fsdp import ShardingStrategy

        strategy_map = {
            "fsdp": ShardingStrategy.FULL_SHARD,
            "hybrid": ShardingStrategy.HYBRID_SHARD,
        }
        sharding_strategy = strategy_map.get(
            self.config.sharding_strategy,
            ShardingStrategy.FULL_SHARD,
        )

        return FSDP(
            model,
            sharding_strategy=sharding_strategy,
            device_id=self.local_rank,
            use_orig_params=True,
        )

    def setup_dataloader(self, dataset: torch.utils.data.Dataset) -> torch.utils.data.DataLoader:
        """设置分布式数据加载器。

        使用 :class:`DistributedSampler` 确保每卡处理不同的数据分片。

        Args:
            dataset: 训练数据集。

        Returns:
            配置好的 DataLoader。
        """
        sampler: torch.utils.data.distributed.DistributedSampler | None = None
        if self.world_size > 1:
            sampler = torch.utils.data.distributed.DistributedSampler(dataset)

        per_gpu_batch = self.config.batch_size // self.world_size
        if per_gpu_batch < 1:
            per_gpu_batch = 1

        dataloader = torch.utils.data.DataLoader(
            dataset,
            batch_size=per_gpu_batch,
            sampler=sampler,
            shuffle=(sampler is None),
            num_workers=4,
            pin_memory=True,
            drop_last=True,
            worker_init_fn=_seed_worker,
        )

        return dataloader

    def setup_optimizer(
        self,
        model: nn.Module,
    ) -> torch.optim.Optimizer:
        """创建优化器。

        Args:
            model: 待优化的模型。

        Returns:
            配置好的 AdamW 优化器。
        """
        optimizer = torch.optim.AdamW(
            model.parameters(),
            lr=self.config.learning_rate,
            weight_decay=self.config.weight_decay,
        )
        return optimizer

    def warmup_lr_schedule(self, step: int) -> float:
        """学习率预热调度。

        Args:
            step: 当前训练步数。

        Returns:
            当前步的学习率缩放因子。
        """
        if step < self.config.warmup_steps:
            return step / max(1, self.config.warmup_steps)
        return 1.0

    def train(
        self,
        model: nn.Module,
        dataloader: torch.utils.data.DataLoader,
        optimizer: torch.optim.Optimizer,
        compute_loss: Callable[[nn.Module, Any], torch.Tensor],
    ) -> dict[str, list[float]]:
        """分布式训练主循环。

        Args:
            model: 分布式模型。
            dataloader: 训练数据加载器。
            optimizer: 优化器。
            compute_loss: 损失计算函数，签名为
                ``compute_loss(model, batch) -> loss_tensor``。

        Returns:
            训练历史字典，包含:
            - ``losses``: 每步的损失值
            - ``learning_rates``: 每步的学习率
        """
        losses: list[float] = []
        learning_rates: list[float] = []

        # 断点续训
        if self.config.resume_from_checkpoint:
            self._load_checkpoint(model, optimizer, self.config.resume_from_checkpoint)
        self._beat(force=True)

        for epoch in range(self._epoch, self.config.epochs):
            self._epoch = epoch

            if self.world_size > 1 and hasattr(dataloader.sampler, "set_epoch"):
                dataloader.sampler.set_epoch(epoch)

            model.train()
            optimizer.zero_grad()

            for batch_idx, batch in enumerate(dataloader):
                # 混合精度前向
                autocast_ctx = (
                    torch.cuda.amp.autocast(dtype=self._autocast_dtype)
                    if self._autocast_dtype is not None
                    else _nullcontext()
                )

                with autocast_ctx:
                    loss = compute_loss(model, batch)
                    loss = loss / self.config.gradient_accumulation_steps

                # 反向传播
                if self._scaler is not None:
                    self._scaler.scale(loss).backward()
                else:
                    loss.backward()

                # 梯度累积
                if (batch_idx + 1) % self.config.gradient_accumulation_steps == 0:
                    # 梯度裁剪（clip_grad_norm_ 返回裁剪前总范数，是梯度尖峰的第一信号源）
                    pre_clip_grad_norm: float | None = None
                    if self.config.max_gradient_norm is not None:
                        if self._scaler is not None:
                            self._scaler.unscale_(optimizer)
                        pre_clip_grad_norm = float(
                            torch.nn.utils.clip_grad_norm_(
                                model.parameters(),
                                self.config.max_gradient_norm,
                            )
                        )
                        if (
                            not math.isfinite(pre_clip_grad_norm)
                            or pre_clip_grad_norm > self.config.max_gradient_norm * _GRAD_NORM_SPIKE_FACTOR
                        ):
                            logger.warning(
                                "梯度范数异常: step=%d, pre_clip_norm=%.4f (阈值=%.4f, 尖峰系数=%d)",
                                self._step,
                                pre_clip_grad_norm,
                                self.config.max_gradient_norm,
                                _GRAD_NORM_SPIKE_FACTOR,
                            )

                    # 学习率预热
                    lr_scale = self.warmup_lr_schedule(self._step)
                    for pg in optimizer.param_groups:
                        pg["lr"] = self.config.learning_rate * lr_scale

                    # 优化器步进
                    if self._scaler is not None:
                        self._scaler.step(optimizer)
                        self._scaler.update()
                    else:
                        optimizer.step()
                    optimizer.zero_grad()

                    self._step += 1
                    losses.append(loss.item() * self.config.gradient_accumulation_steps)
                    learning_rates.append(optimizer.param_groups[0]["lr"])
                    self._beat()

                    # 日志
                    if self.global_rank == 0 and self._step % self.config.log_interval == 0:
                        norm_str = f", GradNorm: {pre_clip_grad_norm:.4f}" if pre_clip_grad_norm is not None else ""
                        logger.info(
                            "Epoch %d/%d, Step %d, Loss: %.4f%s, LR: %.2e",
                            epoch + 1,
                            self.config.epochs,
                            self._step,
                            losses[-1],
                            norm_str,
                            learning_rates[-1],
                        )
                        if self._tracker is not None:
                            metrics: dict[str, float] = {
                                "loss": losses[-1],
                                "lr": learning_rates[-1],
                            }
                            if pre_clip_grad_norm is not None:
                                metrics["grad_norm"] = pre_clip_grad_norm
                            self._tracker.log_metrics(metrics, step=self._step)

                    # 检查点保存
                    if self._step % self.config.checkpoint_interval == 0:
                        self._save_checkpoint(model, optimizer)

            # 每个 epoch 结束后保存检查点
            if self.global_rank == 0:
                self._save_checkpoint(model, optimizer, suffix=f"epoch_{epoch}")
            self._beat(force=True)

        return {"losses": losses, "learning_rates": learning_rates}

    def finish(self) -> None:
        """结束实验追踪会话与心跳（若已启用）。"""
        self._beat(force=True)
        if getattr(self, "_tracker", None) is not None:
            try:
                self._tracker.finish()
            except Exception as e:
                logger.warning("实验追踪器收尾失败: %s", e)
            self._tracker = None

    def _beat(self, force: bool = False) -> None:
        """写入训练心跳（rank 0）。

        每隔 ``heartbeat_interval`` 步把当前进度原子写入心跳文件，
        供外部看门狗判断"进程活着但在干活"还是"已经静默死亡"。
        原子写用临时文件 + rename，避免看门狗读到半截 JSON。

        Args:
            force: True 时无视间隔立即写（用于训练开始/结束）。
        """
        if self.global_rank != 0 or self.config.heartbeat_interval <= 0:
            return
        if not force and self._step - self._last_heartbeat_step < self.config.heartbeat_interval:
            return

        heartbeat_dir = Path(self.config.heartbeat_dir)
        heartbeat_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "step": self._step,
                "epoch": self._epoch,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "pid": os.getpid(),
            }
        )
        tmp_path = heartbeat_dir / "heartbeat.json.tmp"
        final_path = heartbeat_dir / "heartbeat.json"
        try:
            tmp_path.write_text(payload, encoding="utf-8")
            tmp_path.replace(final_path)
            self._last_heartbeat_step = self._step
        except OSError as e:
            logger.warning("心跳写入失败: %s", e)

    def _save_checkpoint(
        self,
        model: nn.Module,
        optimizer: torch.optim.Optimizer,
        suffix: str = "",
    ) -> None:
        """保存训练检查点。

        Args:
            model: 当前模型。
            optimizer: 当前优化器。
            suffix: 检查点文件名后缀。
        """
        if self.global_rank != 0:
            return

        checkpoint_dir = Path(self.config.checkpoint_dir)
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

        # 获取模型状态（处理 DDP/FSDP 包装）
        model_state = model
        if hasattr(model, "module"):
            model_state = model.module

        checkpoint = {
            "model_state_dict": model_state.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
            "step": self._step,
            "epoch": self._epoch,
            "config": self.config.__dict__,
        }
        # AMP GradScaler 状态必须随 checkpoint 持久化，否则断点续训后 loss scale
        # 从头摸索，且崩在 scale 爆炸边缘时会复现不稳定
        if self._scaler is not None:
            checkpoint["scaler_state_dict"] = self._scaler.state_dict()

        filename = f"checkpoint_step_{self._step}"
        if suffix:
            filename += f"_{suffix}"
        filename += ".pt"

        checkpoint_path = checkpoint_dir / filename
        torch.save(
            checkpoint, checkpoint_path
        )  # nosemgrep: pickles-in-pytorch - torch.save 为序列化（非反序列化），非 RCE 向量
        logger.info("检查点已保存: %s", checkpoint_path)
        if getattr(self, "_tracker", None) is not None:
            self._tracker.log_model(str(checkpoint_path), alias=f"step_{self._step}")
        self._prune_step_checkpoints(checkpoint_dir)

    def _prune_step_checkpoints(self, checkpoint_dir: Path) -> None:
        """滚动清理按步保存的旧检查点，仅保留最近 N 个。

        7B 级模型单份全量 checkpoint 可达数十 GB，不清理会在长训练中撑爆磁盘，
        反过来毁掉正在进行的训练。epoch 结尾快照（``*_epoch_*.pt``）不在此清理范围。

        Args:
            checkpoint_dir: 检查点目录。
        """
        keep = self.config.keep_last_checkpoints
        if keep <= 0:
            return

        step_ckpts: list[tuple[int, Path]] = []
        for p in checkpoint_dir.glob("checkpoint_step_*.pt"):
            match = _STEP_CHECKPOINT_PATTERN.match(p.name)
            if match:
                step_ckpts.append((int(match.group(1)), p))

        step_ckpts.sort(key=lambda item: item[0])
        for _, stale in step_ckpts[:-keep] if len(step_ckpts) > keep else []:
            try:
                stale.unlink()
                logger.info("已清理过期检查点: %s", stale)
            except OSError as e:
                logger.warning("清理检查点失败 %s: %s", stale, e)

    def _load_checkpoint(
        self,
        model: nn.Module,
        optimizer: torch.optim.Optimizer,
        checkpoint_path: str,
    ) -> None:
        """加载训练检查点（断点续训）。

        Args:
            model: 待加载权重的模型。
            optimizer: 待加载状态的优化器。
            checkpoint_path: 检查点文件路径。
        """
        checkpoint = torch.load(
            checkpoint_path, map_location=self.device, weights_only=False
        )  # nosemgrep: pickles-in-pytorch - 断点续训加载自产 checkpoint（开发者本地工具，非 Web 攻击面）；加载源受训前配置约束

        # 处理 DDP/FSDP 包装
        model_state = model
        if hasattr(model, "module"):
            model_state = model.module

        model_state.load_state_dict(checkpoint["model_state_dict"])
        optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
        if self._scaler is not None and "scaler_state_dict" in checkpoint:
            self._scaler.load_state_dict(checkpoint["scaler_state_dict"])
        self._step = checkpoint.get("step", 0)
        self._epoch = checkpoint.get("epoch", 0)

        logger.info(
            "检查点已恢复: %s (step=%d, epoch=%d)",
            checkpoint_path,
            self._step,
            self._epoch,
        )

    def cleanup(self) -> None:
        """清理分布式环境。

        在训练结束后调用，销毁分布式进程组。
        """
        import torch.distributed as dist

        if dist.is_initialized():
            dist.destroy_process_group()
            logger.info("分布式环境已清理")

    def evaluate(
        self,
        model: nn.Module,
        dataloader: torch.utils.data.DataLoader,
        compute_metrics: Callable[[nn.Module, Any], dict[str, float]],
    ) -> dict[str, float]:
        """在验证集上评估模型。

        Args:
            model: 待评估的模型。
            dataloader: 验证数据加载器。
            compute_metrics: 指标计算函数，签名为
                ``compute_metrics(model, batch) -> dict``。

        Returns:
            平均指标字典。
        """
        model.eval()
        total_metrics: dict[str, float] = {}
        num_batches = 0

        with torch.no_grad():
            for batch in dataloader:
                metrics = compute_metrics(model, batch)
                for key, value in metrics.items():
                    total_metrics[key] = total_metrics.get(key, 0.0) + value
                num_batches += 1

        avg_metrics = {key: value / max(num_batches, 1) for key, value in total_metrics.items()}

        if self.global_rank == 0:
            metrics_str = ", ".join(f"{k}={v:.4f}" for k, v in avg_metrics.items())
            logger.info("评估结果: %s", metrics_str)

        return avg_metrics


class _nullcontext:
    """简化的 context manager（兼容 Python 3.10+）。

    ``contextlib.nullcontext`` 的轻量替代，用于条件性 autocast。
    """

    def __enter__(self) -> None:
        pass

    def __exit__(self, *args: object) -> None:
        pass


def main() -> None:
    """命令行入口 — 从配置文件启动训练。

    使用方式::

        torchrun --nproc_per_node=4 training/distributed_trainer.py --config config.yaml
    """
    parser = argparse.ArgumentParser(description="分布式训练启动器")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="训练配置 YAML 文件路径",
    )
    parser.add_argument("--batch-size", type=int, default=32, help="全局批次大小")
    parser.add_argument("--epochs", type=int, default=100, help="训练轮数")
    parser.add_argument("--lr", type=float, default=1e-4, help="学习率")
    parser.add_argument(
        "--sharding",
        type=str,
        default="ddp",
        choices=["ddp", "fsdp", "hybrid"],
        help="分片策略",
    )
    parser.add_argument("--mixed-precision", type=str, default=None, choices=["fp16", "bf16"])
    parser.add_argument("--gradient-checkpointing", action="store_true")
    parser.add_argument("--checkpoint-dir", type=str, default="data/checkpoints")
    parser.add_argument("--keep-checkpoints", type=int, default=3, help="保留最近 N 个按步检查点（0=全保留）")
    parser.add_argument("--seed", type=int, default=None, help="基础随机种子（各 rank 自动偏移）")
    parser.add_argument("--track", action="store_true", help="启用实验追踪（本地 JSONL，wandb 可选）")
    parser.add_argument("--experiment-name", type=str, default=None)
    parser.add_argument("--use-wandb", action="store_true", help="实验追踪尝试上传 wandb")
    parser.add_argument("--resume", type=str, default=None, help="断点续训检查点路径")

    args = parser.parse_args()

    # 加载配置文件（如果提供）
    config_kwargs: dict[str, Any] = {
        "batch_size": args.batch_size,
        "epochs": args.epochs,
        "learning_rate": args.lr,
        "sharding_strategy": args.sharding,
        "mixed_precision": args.mixed_precision,
        "gradient_checkpointing": args.gradient_checkpointing,
        "checkpoint_dir": args.checkpoint_dir,
        "keep_last_checkpoints": args.keep_checkpoints,
        "seed": args.seed,
        "enable_experiment_tracking": args.track,
        "experiment_name": args.experiment_name,
        "experiment_use_wandb": args.use_wandb,
        "resume_from_checkpoint": args.resume,
    }

    if args.config:
        import yaml

        with open(args.config) as f:
            yaml_config = yaml.safe_load(f)
        config_kwargs.update(yaml_config)

    config = TrainingConfig(**config_kwargs)

    # 创建训练器（实际训练需要用户提供模型和数据）
    trainer = DistributedTrainer(config)
    logger.info(
        "训练器已创建: world_size=%d, sharding=%s",
        trainer.world_size,
        config.sharding_strategy,
    )
    logger.info("请通过 Python API 调用 trainer.train() 方法开始训练")
    logger.info("配置: %s", config)

    trainer.finish()
    trainer.cleanup()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    main()
