# Contributing to SeedVR2-lite

Thank you for your interest in contributing to SeedVR2-lite — a self-hosted image/video restoration (Restore) tool.

This document gives a short "10-minute quick start" to get contributors productive, and a concise reference for common contribution tasks.

---

## Quick Start (10 minutes)

1. Fork the repo and clone

```bash
git clone https://github.com/ReSerendipity/SeedVR2-lite.git
cd SeedVR2-lite
```

2. Install & run

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
python app/clean_launch.py
```

3. Create a branch for your change

```bash
git checkout -b fix/short-description
# make changes, run tests, then push
git commit -m "fix(restore): short description"
git push origin fix/short-description
```

4. Open a Pull Request using the provided template.

---

## Development (local)

Prerequisites
- Python 3.10+ (3.12 recommended)
- NVIDIA GPU (CUDA) for model inference
- FFmpeg
- Git

Run tests

```bash
pytest tests/ -v
```

Lint/format

```bash
ruff check app/ scripts/
ruff format app/ scripts/
```

---

## How to File Good Issues

- Bug reports: include environment (OS, Python, GPU, CUDA), steps to reproduce, expected vs actual behavior, and logs.
- Feature requests: describe the use case, proposed solution, and any alternatives.

Use the provided issue templates (bug_report / feature_request).

---

## Pull Request Checklist

- Use a descriptive title and include a short summary in the PR body.
- Link related issues using `Closes #<issue>` when appropriate.
- Add tests for new behavior where feasible.
- Run tests & linters locally before opening the PR.
- Follow Conventional Commits for commit messages (`feat:`, `fix:`, `docs:`, etc.).

---

## License

By contributing, you agree your contributions are licensed under the Apache License 2.0 (see [LICENSE](../LICENSE)).

---

Thank you for contributing — the community makes this project better!
