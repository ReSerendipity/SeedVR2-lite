#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2024-2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""生成带 SHA256 哈希锁的 requirements-lock.txt

防御供应链投毒 (CWE-912)：pip install --require-hashes 时会验证每个包的哈希值。

用法:
    # 使用项目虚拟环境 (需已安装全部依赖)
    .venv/Scripts/python.exe scripts/generate_lock.py

    # 或使用系统 Python (需已安装依赖)
    python scripts/generate_lock.py

输出:
    requirements-lock.txt (带 --hash=sha256:... 的锁定版本)

原理:
    1. 从已安装包生成版本锁定列表 (pip freeze)
    2. 优先通过 PyPI JSON API 获取该精确版本全部发布文件的官方 SHA256
       (https://pypi.org/pypi/<name>/<version>/json, 与 pip-tools 同源可信)
    3. 联网不可用时回退到本地 pip 缓存中的 wheel 哈希
    4. 生成 --hash=sha256:xxx 格式的锁定文件

注意:
    - torch/torchvision/torchaudio 使用 CUDA 预编译包 (+cu128), 其发布文件
      托管在 PyTorch 官方 index 而非 PyPI, PyPI JSON API 查不到对应哈希时
      会保留 # NO HASH 标记, 需在有网的 PyTorch index 环境重新生成。
    - 生成的锁文件可用于: pip install --require-hashes -r requirements-lock.txt
"""

import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PYPI_JSON_URL = "https://pypi.org/pypi/{name}/{version}/json"
PYTORCH_INDEX_URL = os.environ.get("SEEDVR2_PYTORCH_INDEX_URL", "https://download.pytorch.org/whl/cu132")
TORCH_FAMILY = {"torch", "torchvision", "torchaudio"}
REQUEST_TIMEOUT_SECONDS = 30


def get_installed_packages():
    """获取已安装包列表及其版本。

    返回 (name, version, hashes) 元组列表。普通 PyPI 包 hashes 为空，稍后查询；
    本地 wheel/file:// 直接安装的包（如 torch+cu132 CUDA 轮子）从 pip freeze
    输出的 #sha256= 片段提取已验证的哈希。
    """
    result = subprocess.run(
        [sys.executable, "-m", "pip", "freeze"],
        capture_output=True,
        text=True,
        check=True,
    )
    packages = []
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        if "@" in line.split("==")[0]:
            # 直接 URL 安装: name @ file:///.../pkg-version-...whl#sha256=<hex>
            name, _, url = line.partition(" @ ")
            filename = os.path.basename(urllib.parse.unquote(url.split("#")[0]))
            sha = ""
            if "#sha256=" in url:
                sha = url.split("#sha256=", 1)[1].split("&")[0]
            # wheel 命名: {name}-{version}-{...}.whl → 版本为第 2 段
            stem = filename[:-4] if filename.endswith(".whl") else filename
            parts = stem.split("-")
            if len(parts) >= 2 and sha:
                packages.append((parts[0].replace("_", "-").lower(), parts[1], [sha]))
            else:
                print(f"  ! 跳过无法解析的直接安装包: {line[:80]}")
            continue
        if "==" in line:
            name, version = line.split("==", 1)
            packages.append((name, version, []))
    return packages


def compute_file_hash(filepath):
    """计算文件的 SHA256 哈希。"""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(8 * 1024 * 1024)
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()


def fetch_hashes_from_pypi(name, version, retries=3):
    """从 PyPI JSON API 获取精确版本全部发布文件的 SHA256。

    网络瞬断/限流时按退避重试；多次失败返回 []（调用方回退本地缓存）。

    Returns:
        list[str]: sha256 十六进制哈希列表; 查询失败或版本不在 PyPI 时返回 []。
    """
    url = PYPI_JSON_URL.format(name=name, version=version)
    import time

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                data = json.load(resp)
            return [f["digests"]["sha256"] for f in data.get("urls", []) if f.get("digests", {}).get("sha256")]
        except Exception as e:  # noqa: BLE001 — 网络层异常类型繁杂，统一按可重试处理
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            print(f"  ! PyPI 查询失败 {name}=={version}: {type(e).__name__}")
            return []
    return []


def fetch_hashes_from_pytorch_index(name, version, retries=3):
    """从 PyTorch 官方 wheel 索引页提取该版本全部平台轮子的 SHA256。

    torch 家族的 +cuXXX 本地版本不在 PyPI 上，其 PEP 503 索引页
    （https://download.pytorch.org/whl/<cu>/<pkg>/）每个 wheel 链接都带
    ``#sha256=<hex>`` 锚点，无需下载轮子本体即可取得全平台官方哈希
    ——这是锁文件能跨 Windows/Linux 双平台 ``--require-hashes`` 安装的前提。

    Returns:
        list[str]: 匹配版本的 sha256 去重列表；索引不可达或无匹配时返回 []。
    """
    import time

    if "+" not in version:
        return []
    url = f"{PYTORCH_INDEX_URL.rstrip('/')}/{name}/"
    prefix = f"{name}-{version.replace('+', '%2B')}-"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"Accept": "text/html"})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            hashes = []
            for match in re.finditer(re.escape(prefix) + r'[^"#\s>]*#sha256=([0-9a-fA-F]{64})', html):
                digest = match.group(1).lower()
                if digest not in hashes:
                    hashes.append(digest)
            if not hashes:
                print(f"  ! PyTorch 索引无 {name}=={version} 的轮子锚点")
            return hashes
        except Exception as e:  # noqa: BLE001 — 网络层异常类型繁杂，统一按可重试处理
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            print(f"  ! PyTorch 索引查询失败 {name}=={version}: {type(e).__name__}")
            return []
    return []


def collect_local_cache_hashes():
    """从本地 pip 缓存收集 wheel 哈希 (联网不可用时的回退)。"""
    try:
        cache_result = subprocess.run(
            [sys.executable, "-m", "pip", "cache", "dir"],
            capture_output=True,
            text=True,
            check=True,
        )
        cache_dir = cache_result.stdout.strip()
    except subprocess.CalledProcessError:
        return {}
    wheels = glob.glob(os.path.join(cache_dir, "**", "*.whl"), recursive=True)
    pkg_hashes = {}
    for whl in wheels:
        basename = os.path.basename(whl)
        parts = basename.split("-")
        if len(parts) >= 2:
            pkg_name = parts[0].replace("_", "-").lower()
            h = compute_file_hash(whl)
            pkg_hashes.setdefault(pkg_name, [])
            if h not in pkg_hashes[pkg_name]:
                pkg_hashes[pkg_name].append(h)
    return pkg_hashes


def generate_lock_file():
    """生成带哈希锁的 requirements 文件。"""
    output_path = Path(__file__).parent.parent / "requirements-lock.txt"

    packages = get_installed_packages()
    print(f"已安装包: {len(packages)} 个")

    local_cache = collect_local_cache_hashes()

    output = []
    output.append("# SeedVR2 依赖哈希锁文件 (CWE-912 供应链投毒防御)")
    output.append("#")
    output.append("# 用途: pip install --require-hashes -r requirements-lock.txt")
    output.append("# 重新生成: python scripts/generate_lock.py")
    output.append("#")
    output.append("# 注意:")
    output.append("#   - torch/torchvision/torchaudio 使用 CUDA 本地版本 (+cu132):")
    output.append("#     索引页带 #sha256 锚点的版本取全平台锚点（Windows/Linux 均可校验），")
    output.append("#     新版本若索引页只提供 PEP 658 元数据（无轮子哈希锚点，如 torch 2.13.0），")
    output.append("#     则保留本地 wheel 哈希（当前为 Windows 轮）——跨平台安装该包前需先补齐哈希")
    output.append("#   - 切换 CUDA 版本时需重新生成哈希")
    output.append("")

    hash_count = 0
    no_hash_count = 0
    no_hash_packages = []
    # pip 续行规则：除最后一行外，链上每行都要以 " \\" 结尾，
    # 否则后续 --hash 行会被当作独立行而忽略（孤儿 hash）。
    continuation_bs = chr(92)
    for name, version, hashes in sorted(packages):
        if not hashes:
            hashes = fetch_hashes_from_pypi(name, version)
            source = "pypi"
            if not hashes:
                hashes = local_cache.get(name.replace("_", "-").lower(), [])
                source = "cache"
        else:
            source = "local-wheel"
        if name in TORCH_FAMILY and "+" in version:
            # +cuXXX 本地版本：把 PyTorch 索引页的全平台锚点合并进来（与本地 wheel
            # 哈希同源应一致），否则锁文件只有本机平台一个哈希，跨平台 CI 必失败。
            index_hashes = fetch_hashes_from_pytorch_index(name, version)
            if index_hashes:
                merged = list(hashes) + [h for h in index_hashes if h not in hashes]
                if hashes and len(merged) == len(index_hashes):
                    print(f"  ! 警告：{name} 本地 wheel 哈希与索引锚点不一致，请人工复核")
                hashes = merged
                source = f"{source}+pytorch-index"

        if hashes:
            hash_lines = [f"    --hash=sha256:{h}" for h in hashes]
            hash_count += len(hash_lines)
            body = (
                [f"{name}=={version} {continuation_bs}"]
                + [hl + f" {continuation_bs}" for hl in hash_lines[:-1]]
                + [hash_lines[-1]]
            )
            output.extend(body)
            print(f"  ✓ {name}=={version} [{source}: {len(hashes)} hashes]")
        else:
            output.append(f"# NO HASH (run generate_lock.py with network): {name}=={version}")
            output.append(f"{name}=={version}")
            no_hash_count += 1
            no_hash_packages.append(f"{name}=={version}")
            print(f"  ✗ {name}=={version} [NO HASH]")

    content = "\n".join(output) + "\n"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"\n已生成: {output_path}")
    print(f"  总包数: {len(packages)}")
    print(f"  含哈希: {hash_count} 个哈希值")
    print(f"  无哈希: {no_hash_count} 个包")
    if no_hash_packages:
        print("  无哈希清单:", ", ".join(no_hash_packages))

    if no_hash_count > 0:
        print()
        print("要为无哈希的包生成哈希，请运行:")
        print("  pip download -d /tmp/pip-wheels -r requirements.txt")
        print("  python scripts/generate_lock.py  # 再次运行即可自动拾取缓存")

    return no_hash_count


if __name__ == "__main__":
    sys.exit(0 if generate_lock_file() == 0 else 1)
