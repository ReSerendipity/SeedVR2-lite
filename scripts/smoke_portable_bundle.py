#!/usr/bin/env python3
"""SeedVR2 便携包解包后冒烟验收。

对**已经解包出来的便携目录**做一次真实端到端验证，作为 GitHub Release 的发布门禁：

    启动便携服务 → 等健康检查 → 取 CSRF 双提交 token → 提交一张小图修复任务
    → 轮询到 completed → 校验输出文件存在且是合法图片 → 关闭服务

关键事实（决定了本脚本的写法）：
    - `POST /api/restore/` 受 CSRFMiddleware 保护（`app/integrated_app/middleware/csrf.py`
      的 `_EXEMPT_POST_PATH_PATTERNS` 只豁免 `/api/system/locale`），必须同时带
      `csrf_token` 签名 cookie 与同值的 `X-CSRF-Token` 头，否则 403。
      因此这里先发一次安全 GET 拿 Set-Cookie，再带着 cookie+header 发 POST。
    - 只用 stdlib（含 zlib 手写 PNG），因此**任何** Python 3.11+ 都能跑，不依赖被测环境。
    - 纯 stdlib 生成测试图，避免为冒烟额外往包里塞测试资产。

两种强度：
    --require-inference  必须真的跑完一次修复（有 GPU 的机器 / 本地验收）
    默认                 允许因「本机无可用 GPU」被后端明确拒绝（503），但打包层面的
                         任何错误（起不来 / 路由 404 / CSRF 403 / import 失败 / 输出损坏）一律失败

用法：
    python scripts/smoke_portable_bundle.py --app-dir <Target>\\SeedVR2-Portable --require-inference
    python scripts/smoke_portable_bundle.py --app-dir ... --python ...\\python.exe

退出码：0 = 验收通过；1 = 失败（并打印定位信息与服务日志尾部）。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import zlib
from pathlib import Path
from typing import Any

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7870
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
SAFE_PROBE_PATHS = ("/api/system/ping", "/api/system/health")


class SmokeError(RuntimeError):
    """冒烟验收中的确定性失败。"""


def make_test_png(width: int = 96, height: int = 96) -> bytes:
    """用 zlib 手写一个合法 PNG（渐变图），不依赖 Pillow。

    Args:
        width: 图像宽度像素。
        height: 图像高度像素。

    Returns:
        bytes: 完整 PNG 文件字节，以标准 PNG 签名开头。
    """
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter type: None
        for x in range(width):
            rows.extend(((x * 255) // max(width - 1, 1), (y * 255) // max(height - 1, 1), 128))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + chunk(b"IEND", b"")
    )


def build_multipart(fields: dict[str, str], file_field: str, filename: str, filedata: bytes) -> tuple[bytes, str]:
    """构造 multipart/form-data 请求体。"""
    boundary = f"----seedvr2smoke{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
        f'filename="{filename}"\r\nContent-Type: image/png\r\n\r\n'.encode() + filedata + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def http_json(
    url: str,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    """发一次 HTTP 请求，返回 (状态码, JSON 或 {} , 响应头小写键)。

    连不上（服务尚未监听 / 连接被拒 / 超时）时不抛异常，而是返回状态 0 并在
    payload 里带 `_conn_error`，由调用方决定是重试还是失败——服务启动期
    「连接被拒」是正常状态，不能当成打包失败。
    """
    req = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    body = b""
    status = 0
    resp_headers: dict[str, str] = {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 固定本机地址
            body = resp.read()
            status = resp.status
            resp_headers = {k.lower(): v for k, v in resp.getheaders()}
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
        resp_headers = {k.lower(): v for k, v in exc.headers.items()}
    except (urllib.error.URLError, OSError) as exc:
        return 0, {"_conn_error": str(exc)}, {}
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except ValueError:
        payload = {"_raw": body[:200].decode("utf-8", errors="replace")}
    return status, payload, resp_headers


def probe_gpu(python_exe: str, cwd: Path) -> dict[str, Any]:
    """在便携解释器里查一次 torch/CUDA 可用性（不启动服务，纯静态事实）。"""
    code = (
        "import json\n"
        "out={}\n"
        "try:\n"
        "    import torch\n"
        "    out['torch_version']=torch.__version__\n"
        "    out['cuda_available']=bool(torch.cuda.is_available())\n"
        "    out['device_count']=int(torch.cuda.device_count()) if out['cuda_available'] else 0\n"
        "    if out['cuda_available']:\n"
        "        out['device_name']=torch.cuda.get_device_name(0)\n"
        "except Exception as exc:\n"
        "    out['error']=repr(exc)\n"
        "print(json.dumps(out))\n"
    )
    proc = subprocess.run(  # noqa: S603
        [python_exe, "-c", code],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    line = (proc.stdout or "").strip().splitlines()
    if not line:
        return {"error": (proc.stderr or "no output").strip()[:400]}
    try:
        return json.loads(line[-1])
    except ValueError:
        return {"error": f"无法解析输出: {line[-1][:200]}"}


def start_server(python_exe: str, app_dir: Path, log_path: Path) -> subprocess.Popen:
    """用便携解释器后台拉起应用，日志重定向到文件。"""
    env = dict(os.environ)
    env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    log_file = log_path.open("wb")  # noqa: SIM115 - 交由子进程继承，结束时统一关闭
    script = app_dir / "app" / "clean_launch.py"
    if not script.is_file():
        log_file.close()
        raise SmokeError(f"找不到启动脚本 {script}（core 组件是否解包？）")
    try:
        return subprocess.Popen(  # noqa: S603
            [python_exe, str(script)],
            cwd=str(app_dir),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
        )
    finally:
        log_file.close()


def stop_server(proc: subprocess.Popen) -> None:
    """关闭服务（连同子进程树），确保冒烟结束后不残留监听进程。"""
    if os.name == "nt":
        subprocess.run(  # noqa: S603
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()


def tail(path: Path, lines: int = 30) -> str:
    """读取文件尾部若干行。"""
    if not path.is_file():
        return "<无日志>"
    text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(text[-lines:])


def wait_ready(base: str, deadline_sec: int, log_path: Path, proc: subprocess.Popen | None = None) -> dict[str, str]:
    """轮询安全探针直到应用就绪，返回拿到的 CSRF cookie 值。"""
    deadline = time.monotonic() + deadline_sec
    attempt = 0
    last = ""
    while time.monotonic() < deadline:
        attempt += 1
        if proc is not None and proc.poll() is not None:
            raise SmokeError(f"服务进程已退出（退出码 {proc.returncode}），从未监听 {base}\n{tail(log_path, 25)}")
        path = SAFE_PROBE_PATHS[attempt % len(SAFE_PROBE_PATHS)]
        status, payload, headers = http_json(f"{base}{path}", timeout=10)
        cookie = headers.get("set-cookie", "")
        if CSRF_COOKIE_NAME in cookie:
            value = cookie.split(f"{CSRF_COOKIE_NAME}=", 1)[1].split(";", 1)[0]
            if status == 200:
                print(f"  应用就绪（{attempt} 次探测，{path} -> {status}），已取得 CSRF token")
                return {CSRF_COOKIE_NAME: value}
        last = f"{path} -> {status} {str(payload)[:120]}"
        if "Traceback" in tail(log_path, 400):
            raise SmokeError(f"应用启动即报错：\n{tail(log_path, 25)}")
        time.sleep(2)
    raise SmokeError(f"等待应用就绪超时（{deadline_sec}s），最后一次：{last}\n日志尾部：\n{tail(log_path, 25)}")


def submit_restore(base: str, token: str, png: bytes, resolution: int, timeout: int) -> str:
    """带 CSRF 双提交上传测试图，返回 task_id。"""
    fields = {
        "task_type": "image",
        "dit_model": "3b_fp8",
        "resolution": str(resolution),
        "max_resolution": str(resolution),
        "seed": "42",
        "double_res": "false",
    }
    body, content_type = build_multipart(fields, "file", "seedvr2_smoke.png", png)
    headers = {
        "Content-Type": content_type,
        "Cookie": f"{CSRF_COOKIE_NAME}={token}",
        CSRF_HEADER_NAME: token,
    }
    status, payload, _ = http_json(f"{base}/api/restore/", method="POST", data=body, headers=headers, timeout=timeout)
    if status == 403:
        raise SmokeError(f"CSRF 403（打包的中间件/配置异常）：{payload}")
    if status != 200:
        raise SmokeError(f"提交修复任务失败 HTTP {status}：{payload}")
    task_id = (payload.get("data") or {}).get("task_id")
    if not task_id:
        raise SmokeError(f"响应缺少 task_id：{payload}")
    return str(task_id)


def classify_submit_failure(message: str) -> tuple[bool, str]:
    """判断提交失败是否属于「本机无可用 GPU」这类可容忍原因。

    Returns:
        (是否可容忍, 说明)
    """
    no_gpu_marks = ("GPU 不可用", "GPU不可用", "503", "CUDA", "No CUDA", "cuda")
    hit = [m for m in no_gpu_marks if m.lower() in message.lower()]
    if hit:
        return True, f"命中无 GPU 特征 {hit}，在无显卡 runner 上属可容忍"
    return False, "非 GPU 原因，视为打包失败"


def poll_result(base: str, task_id: str, deadline_sec: int) -> dict[str, Any]:
    """轮询任务直到 completed/failed 或超时。"""
    deadline = time.monotonic() + deadline_sec
    last_status = ""
    while time.monotonic() < deadline:
        status, payload, _ = http_json(f"{base}/api/restore/{task_id}/result", timeout=30)
        if status != 200:
            raise SmokeError(f"查询结果 HTTP {status}：{payload}")
        data = payload.get("data") or {}
        state = data.get("status")
        if state != last_status:
            print(f"  任务 {task_id} 状态：{state} 进度：{data.get('progress')}")
            last_status = str(state)
        if state == "completed":
            return data
        if state in ("failed", "cancelled"):
            raise SmokeError(f"任务{state}：{data.get('error') or data}")
        time.sleep(3)
    raise SmokeError(f"等待任务完成超时（{deadline_sec}s），最后状态 {last_status}")


def verify_output(data: dict[str, Any], app_dir: Path) -> Path:
    """确认输出文件真实存在且是可解码的图片。"""
    out = data.get("output_path") or data.get("output") or ""
    if not out:
        raise SmokeError(f"completed 但没有输出路径：{data}")
    candidate = Path(out)
    if not candidate.is_absolute():
        candidate = app_dir / out
    if not candidate.is_file():
        hits = sorted(app_dir.rglob(Path(out).name))
        if hits:
            candidate = hits[0]
        else:
            raise SmokeError(f"输出文件不存在：{out}（在 {app_dir} 下也没找到）")
    head = candidate.read_bytes()[:8]
    if not head.startswith(b"\x89PNG") and head[:2] != b"\xff\xd8":
        raise SmokeError(f"输出文件不是 PNG/JPEG（头部 {head[:4]!r}）：{candidate}")
    if candidate.stat().st_size < 1024:
        raise SmokeError(f"输出文件过小（{candidate.stat().st_size} B），疑似空图：{candidate}")
    print(f"  输出校验通过：{candidate}  {candidate.stat().st_size / 1024:.1f} KB")
    return candidate


def compute_quality(python_exe: str, app_dir: Path, input_path: Path, output_path: Path) -> dict[str, float]:
    """用便携解释器（含 numpy/PIL）计算修复输出相对原始输入的 PSNR/SSIM。

    复用应用内置的 ``app.integrated_app.utils.image_metrics``（与 CI 期 golden
    质量门禁同一套口径），保证「冒烟质量门禁」与「开发期质量门禁」指标一致。

    输出会被缩放到输入尺寸后再比较（修复常伴随上采样），只衡量内容保真度，
    用于拦截灾难性失败（黑屏 / 冻结模型 / NaN / 内容错乱）。

    Args:
        python_exe: 便携解释器路径（含 numpy/PIL/torch）。
        app_dir: 解包目录（其下 ``app/`` 含 integrated_app 包）。
        input_path: 原始输入图路径。
        output_path: 修复输出图路径。

    Returns:
        ``{"psnr_db": float, "ssim": float}``；图像无法解码或依赖缺失时返回
        ``{"error": ...}``，交由调用方判失败。
    """
    import tempfile

    snippet = """
import importlib.util, json, os, sys
from pathlib import Path
app_dir = sys.argv[1]
input_path = sys.argv[2]
output_path = sys.argv[3]
# 直接按文件路径加载 image_metrics，避免触发 integrated_app.utils 包 __init__
# 的潜在重导入（与 CI golden 门禁同一套 PSNR/SSIM 实现）
img_mod = os.path.join(app_dir, 'app', 'integrated_app', 'utils', 'image_metrics.py')
spec = importlib.util.spec_from_file_location("seedvr2_image_metrics", img_mod)
metrics_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics_mod)
try:
    from PIL import Image
    import numpy as np
    psnr = metrics_mod.psnr
    ssim = metrics_mod.ssim
except Exception as exc:
    print(json.dumps({"error": repr(exc)}))
    sys.exit(2)
try:
    inp = np.array(Image.open(input_path).convert('RGB'))
    out0 = Image.open(output_path).convert('RGB')
    if out0.size != (inp.shape[1], inp.shape[0]):
        out0 = out0.resize((inp.shape[1], inp.shape[0]))
    out = np.array(out0)
    print(json.dumps({"psnr_db": psnr(inp, out), "ssim": ssim(inp, out)}))
except Exception as exc:
    print(json.dumps({"error": repr(exc)}))
    sys.exit(3)
"""
    tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False, dir=str(app_dir))  # noqa: SIM115 - 用完即删
    tmp.write(snippet.encode("utf-8"))
    tmp.close()
    try:
        proc = subprocess.run(  # noqa: S603
            [python_exe, tmp.name, str(app_dir), str(input_path), str(output_path)],
            cwd=str(app_dir),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        out = (proc.stdout or "").strip().splitlines()
        if not out:
            return {"error": (proc.stderr or "no output").strip()[:300]}
        return json.loads(out[-1])
    except (ValueError, json.JSONDecodeError) as exc:
        return {"error": f"无法解析质量评估输出: {exc}"}
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp.name)


def passes_quality_gate(metrics: dict[str, float], min_psnr: float, min_ssim: float) -> tuple[bool, str]:
    """判定真实推理输出是否满足质量门禁。

    Args:
        metrics: ``compute_quality`` 的返回（含 psnr_db/ssim 或 error）。
        min_psnr: PSNR 下限（dB）。
        min_ssim: SSIM 下限。

    Returns:
        (是否通过, 人类可读说明)。
    """
    if "error" in metrics:
        return False, f"质量评估失败：{metrics['error']}"
    psnr_db = float(metrics.get("psnr_db", 0.0))
    ssim_v = float(metrics.get("ssim", 0.0))
    reasons: list[str] = []
    if psnr_db < min_psnr:
        reasons.append(f"PSNR {psnr_db:.2f}dB < {min_psnr:.2f}dB")
    if ssim_v < min_ssim:
        reasons.append(f"SSIM {ssim_v:.4f} < {min_ssim:.4f}")
    if reasons:
        return False, "；".join(reasons)
    return True, f"PSNR {psnr_db:.2f}dB / SSIM {ssim_v:.4f}"


def find_python(app_dir: Path, explicit: str) -> str:
    """确定用于跑冒烟的 python：显式参数 → 便携 WPy64 → 当前解释器。"""
    if explicit:
        return explicit
    for py in sorted(app_dir.glob("WPy64-*/python*/python.exe")) + sorted(app_dir.glob("WPy64-*/python/python.exe")):
        if py.is_file():
            return str(py)
    print("  警告：未找到便携解释器，退回当前 python（可能缺少应用依赖）")
    return sys.executable


def run(args: argparse.Namespace) -> int:
    """执行完整冒烟验收，返回退出码。"""
    app_dir = Path(args.app_dir).resolve()
    if not app_dir.is_dir():
        print(f"FAIL  目录不存在：{app_dir}")
        return 1
    if args.python:
        python_exe = args.python
    else:
        python_exe = find_python(app_dir, "")
    base = f"http://{args.host}:{args.port}"
    log_path = Path(args.log) if args.log else app_dir / "logs" / "smoke_portable.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"\n== 便携包冒烟验收 ==\n  目录：{app_dir}\n  解释器：{python_exe}\n  地址：{base}")
    gpu = probe_gpu(python_exe, app_dir)
    print(f"  GPU 探测：{gpu}")
    if gpu.get("error"):
        print(f"FAIL  便携解释器 import torch 失败：{gpu['error']}")
        return 1

    proc = None
    token_value = ""
    try:
        proc = start_server(python_exe, app_dir, log_path)
        try:
            cookies = wait_ready(base, args.boot_timeout, log_path, proc)
            token_value = cookies.get(CSRF_COOKIE_NAME, "")
            if not token_value:
                raise SmokeError("未能从响应头取得 csrf_token cookie")

            png = make_test_png()
            print(f"  测试图：{len(png)} 字节 PNG")
            try:
                task_id = submit_restore(base, token_value, png, args.resolution, args.submit_timeout)
            except SmokeError as exc:
                tolerable, why = classify_submit_failure(str(exc))
                if tolerable and not args.require_inference:
                    print(f"WARN  未跑通推理但属可容忍原因：{why}\n      {exc}")
                    print("  打包层面验收通过：服务可启动 + 路由可达 + CSRF 链路正确 + torch 可导入")
                    print("      （要强制真跑一次推理，请加 --require-inference）")
                    return 0
                raise
            print(f"  任务已创建：{task_id}")
            data = poll_result(base, task_id, args.task_timeout)
            output_path = verify_output(data, app_dir)
            if args.quality_gate:
                # 真实推理输出保真度校验：输出应与原输入内容高度一致
                # （修复是内容保持型上采样，而非重绘），拦截黑屏/冻结/NaN/错乱
                input_path = app_dir / "logs" / "smoke_input.png"
                input_path.parent.mkdir(parents=True, exist_ok=True)
                input_path.write_bytes(png)
                metrics = compute_quality(python_exe, app_dir, input_path, output_path)
                ok, why = passes_quality_gate(metrics, args.min_psnr, args.min_ssim)
                if not ok:
                    raise SmokeError(f"GPU 真实推理质量门禁未通过：{why}")
                print(f"  质量门禁通过：{why}")
            elapsed = data.get("processing_time") or data.get("elapsed")
            print(f"  推理耗时：{elapsed}")
            print(f"PASS  解包后可真实完成一次修复（{'required' if args.require_inference else 'observed'}）")
            return 0
        finally:
            if proc is not None:
                stop_server(proc)
            if args.keep_log and log_path.is_file():
                print(f"  服务日志：{log_path}")
    except SmokeError as exc:
        print(f"FAIL  {exc}")
        print("  ---- 服务日志尾部 ----")
        print(tail(log_path, 30))
        return 1
    except Exception as exc:  # noqa: BLE001 - 冒烟为边界脚本，兜底打印定位信息
        print(f"FAIL  未预期异常：{exc!r}")
        print("  ---- 服务日志尾部 ----")
        print(tail(log_path, 30))
        return 1
    finally:
        if proc is not None and proc.poll() is None:
            stop_server(proc)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="便携包解包后冒烟验收（可当发布门禁）")
    parser.add_argument("--app-dir", required=True, help="解包出的 SeedVR2-Portable 目录")
    parser.add_argument("--python", default="", help="指定 python（默认自动取便携 WPy64 解释器）")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--boot-timeout", type=int, default=600, help="等待应用就绪的秒数（首次含模型加载）")
    parser.add_argument("--submit-timeout", type=int, default=180)
    parser.add_argument("--task-timeout", type=int, default=1800, help="等待修复任务完成的秒数")
    parser.add_argument("--resolution", type=int, default=512, help="冒烟任务目标分辨率，小才快")
    parser.add_argument("--log", default="", help="服务日志路径，默认 <app-dir>/logs/smoke_portable.log")
    parser.add_argument("--require-inference", action="store_true", help="必须真跑完一次修复（有 GPU 时使用）")
    parser.add_argument(
        "--quality-gate",
        action="store_true",
        help="真实推理后校验输出保真度（PSNR/SSIM 不低于阈值），仅在有真实输出时生效",
    )
    parser.add_argument(
        "--min-psnr", type=float, default=15.0, help="质量门禁 PSNR 下限(dB)，默认 15（拦截黑屏/冻结/NaN）"
    )
    parser.add_argument("--min-ssim", type=float, default=0.5, help="质量门禁 SSIM 下限，默认 0.5")
    parser.add_argument("--keep-log", action="store_true", help="结束时打印日志路径")
    return parser.parse_args(argv)


def force_utf8_output() -> None:
    """把 stdout/stderr 切成 UTF-8。

    Windows 控制台或 CI runner 的默认编码可能是 cp1252/GBK，本脚本输出含中文，
    不显式改就会在 print 处抛 UnicodeEncodeError，把「验收通过」误报成失败。
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    force_utf8_output()
    sys.exit(run(parse_args()))
