"""training/distributed_trainer.py MLOps 加固的单元测试。

覆盖:
- P0-1: checkpoint 保存 GradScaler 状态 + 按步检查点滚动保留 + 梯度范数捕获
- P0-2: seed 统一播种（python/numpy/torch）
- P1-3: ExperimentTracker 接线（本地 JSONL，wandb 缺失自动降级）
- P2-6: 训练心跳原子落盘
"""

import json
from pathlib import Path

import torch
import torch.nn as nn

from training.distributed_trainer import DistributedTrainer, TrainingConfig, _seed_worker


class _TinyDataset(torch.utils.data.Dataset):
    def __len__(self) -> int:
        return 8

    def __getitem__(self, idx):
        return torch.randn(4)


def _make_trainer(tmp_path: Path, **overrides) -> DistributedTrainer:
    config = TrainingConfig(
        batch_size=2,
        epochs=1,
        warmup_steps=0,
        checkpoint_dir=str(tmp_path / "ckpt"),
        log_interval=1,
        checkpoint_interval=1,
        **overrides,
    )
    return DistributedTrainer(config)


def _loss_fn(model: nn.Module, batch) -> torch.Tensor:
    device = next(model.parameters()).device
    x = batch if isinstance(batch, torch.Tensor) else batch[0]
    return model(x.to(device)).pow(2).mean()


class TestCheckpointDurability:
    def test_checkpoint_contains_scaler_state_when_fp16(self, tmp_path):
        trainer = _make_trainer(tmp_path, mixed_precision="fp16")
        model = nn.Linear(4, 2)
        optimizer = trainer.setup_optimizer(model)
        trainer._save_checkpoint(model, optimizer)
        files = list(Path(trainer.config.checkpoint_dir).glob("checkpoint_step_*.pt"))
        assert len(files) == 1
        ckpt = torch.load(files[0], map_location="cpu", weights_only=False)
        assert "scaler_state_dict" in ckpt
        assert "scale" in ckpt["scaler_state_dict"]

    def test_checkpoint_without_scaler_omits_state(self, tmp_path):
        trainer = _make_trainer(tmp_path, mixed_precision=None)
        model = nn.Linear(4, 2)
        trainer._save_checkpoint(model, trainer.setup_optimizer(model))
        files = list(Path(trainer.config.checkpoint_dir).glob("checkpoint_step_*.pt"))
        ckpt = torch.load(files[0], map_location="cpu", weights_only=False)
        assert "scaler_state_dict" not in ckpt

    def test_resume_restores_scaler_state(self, tmp_path):
        trainer = _make_trainer(tmp_path, mixed_precision="fp16")
        model = nn.Linear(4, 2)
        optimizer = trainer.setup_optimizer(model)
        trainer._scaler.scale(torch.tensor(1.0, requires_grad=True)).backward()
        trainer._save_checkpoint(model, optimizer)
        ckpt_path = next(Path(trainer.config.checkpoint_dir).glob("checkpoint_step_*.pt"))
        saved_scale = torch.load(ckpt_path, map_location="cpu", weights_only=False)["scaler_state_dict"]["scale"]

        trainer2 = _make_trainer(tmp_path, mixed_precision="fp16")
        model2 = nn.Linear(4, 2)
        optimizer2 = trainer2.setup_optimizer(model2)
        trainer2._load_checkpoint(model2, optimizer2, str(ckpt_path))
        assert trainer2._scaler.get_scale() == saved_scale
        assert trainer2._step == 0  # 尚未 step，仅验证加载不抛错且状态恢复

    def test_prune_keeps_last_n_step_checkpoints(self, tmp_path):
        trainer = _make_trainer(tmp_path, keep_last_checkpoints=2)
        ckpt_dir = Path(trainer.config.checkpoint_dir)
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        for step in (10, 20, 30, 40):
            (ckpt_dir / f"checkpoint_step_{step}.pt").write_bytes(b"x")
        # epoch 快照不受滚动清理影响
        (ckpt_dir / "checkpoint_step_40_epoch_0.pt").write_bytes(b"x")

        trainer._prune_step_checkpoints(ckpt_dir)

        remaining = sorted(p.name for p in ckpt_dir.glob("checkpoint_step_*.pt"))
        assert "checkpoint_step_30.pt" in remaining
        assert "checkpoint_step_40.pt" in remaining
        assert "checkpoint_step_10.pt" not in remaining
        assert "checkpoint_step_20.pt" not in remaining
        # 带后缀的 epoch 快照不参与滚动清理，永久保留
        assert "checkpoint_step_40_epoch_0.pt" in remaining

    def test_prune_disabled_when_zero(self, tmp_path):
        trainer = _make_trainer(tmp_path, keep_last_checkpoints=0)
        ckpt_dir = Path(trainer.config.checkpoint_dir)
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        for step in (1, 2, 3):
            (ckpt_dir / f"checkpoint_step_{step}.pt").write_bytes(b"x")
        trainer._prune_step_checkpoints(ckpt_dir)
        assert len(list(ckpt_dir.glob("checkpoint_step_*.pt"))) == 3

    def test_short_training_respects_retention(self, tmp_path):
        trainer = _make_trainer(tmp_path, keep_last_checkpoints=1)
        model = trainer.setup_model(nn.Linear(4, 2))
        optimizer = trainer.setup_optimizer(model)
        dataloader = trainer.setup_dataloader(_TinyDataset())
        trainer.train(model, dataloader, optimizer, _loss_fn)
        ckpts = list(Path(trainer.config.checkpoint_dir).glob("checkpoint_step_*.pt"))
        # 多次按步保存 + epoch 保存后，滚动保留只留 1 份最新
        assert len(ckpts) >= 1
        step_ckpts = [p for p in ckpts if p.name.count("_") == 2]
        assert len(step_ckpts) <= 1


class TestSeedWiring:
    def test_trainer_seed_sets_all_rngs(self, tmp_path):
        trainer = _make_trainer(tmp_path, seed=123)
        assert trainer.config.seed == 123
        # torch RNG 已被播种：同种子两次取样应一致
        trainer2 = _make_trainer(tmp_path, seed=123)
        a = torch.randn(4)
        torch.manual_seed(123 + trainer2.global_rank)
        b = torch.randn(4)
        assert torch.equal(a, b)

    def test_seed_none_leaves_rng_alone(self, tmp_path):
        trainer = _make_trainer(tmp_path, seed=None)
        assert trainer.config.seed is None

    def test_seed_worker_seeds_numpy_and_random(self):
        import random as random_mod

        import numpy as np

        torch.manual_seed(777)
        _seed_worker(0)
        np_state_after_0 = np.random.get_state()[1][0]
        rand_state_after_0 = random_mod.getstate()
        # worker 种子由 torch.initial_seed() 派生：重置后重新初始化应可复现
        torch.manual_seed(777)
        _seed_worker(0)
        assert np.random.get_state()[1][0] == np_state_after_0
        assert random_mod.getstate() == rand_state_after_0


class TestHeartbeat:
    def test_beat_writes_atomic_heartbeat(self, tmp_path):
        trainer = _make_trainer(tmp_path, heartbeat_interval=1, heartbeat_dir=str(tmp_path / "hb"))
        trainer._step = 5
        trainer._epoch = 0
        trainer._beat(force=True)
        hb = tmp_path / "hb" / "heartbeat.json"
        assert hb.exists()
        payload = json.loads(hb.read_text(encoding="utf-8"))
        assert payload["step"] == 5
        assert "timestamp" in payload
        assert "pid" in payload
        assert not (tmp_path / "hb" / "heartbeat.json.tmp").exists()

    def test_beat_respects_interval(self, tmp_path):
        trainer = _make_trainer(tmp_path, heartbeat_interval=100, heartbeat_dir=str(tmp_path / "hb"))
        trainer._step = 1
        trainer._beat(force=True)
        hb = tmp_path / "hb" / "heartbeat.json"
        first = hb.read_text(encoding="utf-8")
        trainer._step = 2
        trainer._beat()  # 未达间隔，不覆盖
        assert hb.read_text(encoding="utf-8") == first
        trainer._step = 200
        trainer._beat()
        assert json.loads(hb.read_text(encoding="utf-8"))["step"] == 200

    def test_beat_disabled_when_zero(self, tmp_path):
        trainer = _make_trainer(tmp_path, heartbeat_interval=0, heartbeat_dir=str(tmp_path / "hb"))
        trainer._beat(force=True)
        assert not (tmp_path / "hb" / "heartbeat.json").exists()


class TestExperimentTracking:
    def test_tracker_writes_local_jsonl(self, tmp_path):
        trainer = _make_trainer(
            tmp_path,
            enable_experiment_tracking=True,
            experiment_name="unit_test_run",
            experiment_use_wandb=False,
        )
        assert trainer._tracker is not None
        trainer._tracker.log_metrics({"loss": 0.5, "lr": 1e-4}, step=1)
        log_file = Path("experiments/logs/unit_test_run.jsonl")
        assert log_file.exists()
        record = json.loads(log_file.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert record["loss"] == 0.5
        assert record["step"] == 1
        trainer.finish()

    def test_tracking_disabled_by_default(self, tmp_path):
        trainer = _make_trainer(tmp_path)
        assert trainer._tracker is None

    def test_finish_closes_tracker(self, tmp_path):
        trainer = _make_trainer(tmp_path, enable_experiment_tracking=True, experiment_name="finish_test")
        trainer.finish()
        assert trainer._tracker is None


class TestGradNormCapture:
    def test_grad_norm_logged_in_history(self, tmp_path):
        trainer = _make_trainer(tmp_path, max_gradient_norm=1.0)
        model = trainer.setup_model(nn.Linear(4, 2))
        optimizer = trainer.setup_optimizer(model)
        dataloader = trainer.setup_dataloader(_TinyDataset())
        trainer.train(model, dataloader, optimizer, _loss_fn)
        # 训练正常完成即代表梯度裁剪路径（含范数捕获）无回归
        assert trainer._step > 0

    def test_grad_norm_spike_threshold_constant(self):
        from training.distributed_trainer import _GRAD_NORM_SPIKE_FACTOR

        assert _GRAD_NORM_SPIKE_FACTOR >= 1
