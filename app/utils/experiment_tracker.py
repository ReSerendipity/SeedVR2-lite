"""
WandB 实验追踪集成
"""

import json
import logging
import os
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# 检查 wandb 是否可用
try:
    import wandb

    WANDB_AVAILABLE = True
except ImportError:
    WANDB_AVAILABLE = False
    logger.warning("wandb 未安装，使用本地追踪模式")


class ExperimentTracker:
    """实验追踪器 - 支持 WandB 和本地回退"""

    def __init__(
        self,
        project_name: str = "seedvr2-experiments",
        experiment_name: str | None = None,
        config: dict[str, Any] | None = None,
        use_wandb: bool = True,
        local_log_dir: str = "./experiments/logs",
    ):
        self.use_wandb = use_wandb and WANDB_AVAILABLE
        self.local_log_dir = local_log_dir
        os.makedirs(local_log_dir, exist_ok=True)

        # 生成本地实验 ID
        self.exp_id = experiment_name or f"exp_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.local_log_path = os.path.join(local_log_dir, f"{self.exp_id}.jsonl")

        if self.use_wandb:
            try:
                wandb.init(project=project_name, name=self.exp_id, config=config or {})
                logger.info(f"✅ WandB 实验已初始化: {self.exp_id}")
            except Exception as e:
                logger.error(f"WandB 初始化失败: {e}")
                self.use_wandb = False

        # 本地日志文件
        self._log_file = open(self.local_log_path, "a", encoding="utf-8")
        logger.info(f"📝 本地日志: {self.local_log_path}")

    def log_metrics(self, metrics: dict[str, Any], step: int | None = None):
        """记录指标"""
        record = {"timestamp": datetime.now().isoformat(), "step": step, **metrics}

        # 写入本地
        self._log_file.write(json.dumps(record) + "\n")
        self._log_file.flush()

        # 上传 WandB
        if self.use_wandb:
            try:
                wandb.log(metrics, step=step)
            except Exception as e:
                logger.error(f"WandB log failed: {e}")

    def log_hyperparameters(self, config: dict[str, Any]):
        """记录超参数"""
        if self.use_wandb:
            wandb.config.update(config)
        self.log_metrics({"hyperparameters": config}, step=0)

    def log_dataset_manifest(self, manifest: dict[str, Any]):
        """记录训练数据集清单（数据治理 P2-1：训练数据 → 权重可追溯）。

        完整清单写入本地 JSONL（可能很大，故不做裁剪），wandb 侧只上报
        摘要字段（dataset_sha256 / total_files / total_bytes / root），
        避免把成千上万条文件记录推到远端配置里。

        Args:
            manifest: training/dataset_manifest.build_manifest 的返回值。
        """
        if not isinstance(manifest, dict):
            logger.warning("数据集清单格式非法（应为 dict），已跳过记录")
            return

        digest = manifest.get("dataset_sha256") or ""
        summary = {
            "dataset_sha256": digest,
            "dataset_root": manifest.get("root", ""),
            "dataset_total_files": manifest.get("total_files", 0),
            "dataset_total_bytes": manifest.get("total_bytes", 0),
            "dataset_truncated": manifest.get("truncated", False),
            "dataset_manifest_schema": manifest.get("schema", ""),
        }
        if self.use_wandb:
            try:
                wandb.config.update(summary)
            except Exception as e:
                logger.error(f"WandB dataset manifest update failed: {e}")
        # 完整清单落本地（step=0，便于与超参记录对齐检索）
        self.log_metrics({"dataset_manifest": manifest, **summary}, step=0)
        logger.info(f"数据集清单已记录: {summary['dataset_total_files']} 文件, digest={digest[:12]}...")

    def log_model(self, model_path: str, alias: str = "latest"):
        """记录模型文件"""
        if self.use_wandb:
            wandb.save(model_path)
        self.log_metrics({"model_saved": model_path, "alias": alias})

    def log_image(self, image_path: str, caption: str | None = None):
        """记录图像"""
        if self.use_wandb:
            wandb.log({"image": wandb.Image(image_path, caption=caption)})

    def finish(self):
        """结束实验"""
        if self.use_wandb:
            wandb.finish()
        if self._log_file:
            self._log_file.close()
        logger.info(f"实验 {self.exp_id} 已结束")


# 全局追踪器
_tracker: ExperimentTracker | None = None


def get_tracker() -> ExperimentTracker | None:
    """获取全局追踪器"""
    return _tracker


def init_tracker(**kwargs) -> ExperimentTracker:
    """初始化全局追踪器"""
    global _tracker
    _tracker = ExperimentTracker(**kwargs)
    return _tracker
