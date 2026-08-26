# 「紫焰流金」配色方案应用计划

## 概述

将 SeedVR2 项目从当前薰衣草紫色系（`#9b8ec4` / `#7c6fad`）切换为「紫焰流金」配色方案（`#6A5ACD` 菖蒲紫），涉及 **2 个文件、约 80 个 CSS 变量** 的更新。

**源色定义:**
- Primary: `#6A5ACD` (菖蒲紫)
- Secondary: `#FF7F50` (珊瑚橙)
- Accent: `#FFD429` (金)
- Complementary: `#5D4A7C` (暮山紫)

---

## 修改文件清单

| 文件 | 修改内容 |
|------|---------|
| `bin/integrated_app/static/css/style.css` | 暗色 + 亮色两套 CSS 变量全面更新 |
| `bin/integrated_app/templates/base.html` | favicon 和 apple-touch-icon SVG 渐变色更新 |

---

## 实施步骤

### 步骤 1: 更新 CSS 暗色主题变量

**文件:** `bin/integrated_app/static/css/style.css`
**范围:** `[data-theme="dark"]` 块（第 5-132 行）

核心色值映射（基于 theme-selector.html 的 genTheme 逻辑）:

**主色色阶:**

| 变量 | 旧值 | 新值 | 说明 |
|------|------|------|------|
| `--sv-primary` | `#9b8ec4` | `#6A5ACD` | 菖蒲紫 |
| `--sv-primary-50` | `#f5f2ff` | `#F3F2FB` | `li(p,.92)` |
| `--sv-primary-100` | `#ebe6ff` | `#E9E6F8` | `li(p,.85)` |
| `--sv-primary-200` | `#d4cbf0` | `#D5D1F1` | `li(p,.72)` |
| `--sv-primary-300` | `#bfb5e0` | `#BCB5E9` | `li(p,.55)` |
| `--sv-primary-hover` | `#b0a4d4` | `#887BD7` | ipL = `li(p,.20)` |
| `--sv-primary-400` | `#a89cd0` | `#8F83DA` | `li(p,.25)` |
| `--sv-primary-500` | `#9b8ec4` | `#6A5ACD` | 本身 |
| `--sv-primary-600` | `#8a7db5` | `#5F51B9` | `dk(p,.10)` |
| `--sv-primary-active` | `#8a7db5` | `#5F51B9` | 同 600 |
| `--sv-primary-700` | `#796ca6` | `#5346A0` | `dk(p,.22)` |
| `--sv-primary-800` | `#685b97` | `#453B85` | `dk(p,.35)` |
| `--sv-primary-900` | `#574a88` | `#352D67` | `dk(p,.50)` |
| `--sv-primary-dim` | `rgba(155,142,196,.18)` | `rgba(106,90,205,.18)` | pBg |
| `--sv-primary-glow` | `rgba(155,142,196,.35)` | `rgba(106,90,205,.30)` | glow |
| `--sv-primary-glow-strong` | `rgba(155,142,196,.55)` | `rgba(106,90,205,.50)` | glow-strong |

**品牌辅助色:**

| 变量 | 旧值 | 新值 | 映射角色 |
|------|------|------|---------|
| `--sv-accent-purple` | `#b8a9d4` | `#5D4A7C` | 暮山紫(互补色) |
| `--sv-accent-purple-dim` | `rgba(184,169,212,.18)` | `rgba(93,74,124,.18)` | |
| `--sv-accent-pink` | `#c4a9d4` | `#FF7F50` | 珊瑚橙(辅色) |
| `--sv-accent-pink-dim` | `rgba(196,169,212,.18)` | `rgba(255,127,80,.18)` | |
| `--sv-accent-cyan` | `#a89bd0` | `#FFD429` | 金色(点缀色) |
| `--sv-accent-cyan-dim` | `rgba(168,155,208,.18)` | `rgba(255,212,41,.18)` | |
| `--sv-accent-indigo` | `#9b8ec4` | `#887BD7` | 主色亮变体 |
| `--sv-accent-indigo-dim` | `rgba(155,142,196,.18)` | `rgba(136,123,215,.18)` | |

**背景/表面/边框（从主色派生）:**

| 变量 | 旧值 | 新值 | 公式 |
|------|------|------|------|
| `--sv-bg-base` | `#0a0b10` | `#171528` | mix(p,'#0c0c12',.88) |
| `--sv-bg-surface` | `#12141e` | `#221F38` | mix(p,'#15151e',.85) |
| `--sv-bg-elevated` | `#1a1d2e` | `#282642` | mix(p,'#1a1a24',.82) |
| `--sv-bg-overlay` | `#222540` | `#393751` | li(sfH,.08) |
| `--sv-bg-hover` | `#282c47` | `#424059` | li(sfH,.12) |
| `--sv-bg-active` | `#323660` | `#4F4D64` | li(sfH,.18) |
| `--sv-bg-glass` | `rgba(18,20,30,.85)` | `rgba(34,31,56,.85)` | 基于 sf |
| `--sv-bg-glass-strong` | `rgba(18,20,30,.95)` | `rgba(34,31,56,.95)` | 基于 sf |
| `--sv-surface-0` | `#0c0d14` | `#1E1C2E` | li(bg,.03) |
| `--sv-surface-1` | `#141625` | `#221F38` | = sf |
| `--sv-surface-2` | `#1c1f35` | `#282642` | = sfH |
| `--sv-surface-3` | `#252845` | `#35334D` | li(sfH,.06) |
| `--sv-surface-4` | `#2a2d4a` | `#393751` | = overlay |
| `--sv-border` | `rgba(255,255,255,.05)` | `rgba(106,90,205,.06)` | 主色淡入 |
| `--sv-border-light` | `rgba(255,255,255,.08)` | `rgba(106,90,205,.10)` | |
| `--sv-border-hover` | `rgba(255,255,255,.14)` | `rgba(106,90,205,.18)` | |
| `--sv-border-active` | `rgba(255,255,255,.18)` | `rgba(106,90,205,.22)` | |

**文字色（来自 genTheme tx1/tx2/tx3）:**

| 变量 | 旧值 | 新值 |
|------|------|------|
| `--sv-text-primary` | `#e8ecf2` | `#e8e8ec` |
| `--sv-text-secondary` | `#99a6b8` | `#a0a0aa` |
| `--sv-text-muted` | `#7a8a9e` | `#6a6a74` |
| `--sv-text-inverse` | `#0a0b10` | `#171528` |
| `--sv-text-placeholder` | `#5a6a7e` | `#808089` |

**按钮/Tab/Glass:**

| 变量 | 旧值 | 新值 |
|------|------|------|
| `--sv-btn-primary-text` | `#0a0b10` | `#171528` |
| `--sv-tab-active-text` | `#0a0b10` | `#171528` |
| `--sv-glass-bg` | `rgba(18,20,30,.8)` | `rgba(34,31,56,.8)` |
| `--sv-glass-border` | `rgba(255,255,255,.06)` | `rgba(106,90,205,.08)` |

**不变项:** 语义色（success/warning/danger/info）、阴影结构（引用变量自动更新）、滚动条、开关、对比滑块。

---

### 步骤 2: 更新 CSS 亮色主题变量

**范围:** `[data-theme="light"]` 块（第 135-247 行）

**主色色阶（亮色模式同值）:**

| 变量 | 旧值 | 新值 |
|------|------|------|
| `--sv-primary` | `#7c6fad` | `#6A5ACD` |
| `--sv-primary-hover` | `#8d80be` | `#8578D6` (ipL, dk by .18) |
| `--sv-primary-dim` | `rgba(124,111,173,.12)` | `rgba(106,90,205,.10)` |
| `--sv-primary-glow` | `rgba(124,111,173,.20)` | `rgba(106,90,205,.20)` |
| `--sv-primary-glow-strong` | `rgba(124,111,173,.35)` | `rgba(106,90,205,.35)` |

其余色阶与暗色模式相同（50-900 值一致）。

**品牌辅助色（亮色）:**

| 变量 | 旧值 | 新值 |
|------|------|------|
| `--sv-accent-purple` | `#6b5e9c` | `#5D4A7C` |
| `--sv-accent-pink` | `#8a6fad` | `#FF7F50` |
| `--sv-accent-cyan` | `#6f6aad` | `#FFD429` |
| `--sv-accent-indigo` | `#7c6fad` | `#8578D6` |

**背景/表面（亮色从主色派生）:**

| 变量 | 旧值 | 新值 | 公式 |
|------|------|------|------|
| `--sv-bg-base` | `#f8fafc` | `#E0DEF0` | mix(p,'#f0f0f5',.88) |
| `--sv-bg-surface` | `#ffffff` | `#E3E0F5` | mix(p,'#f8f8fc',.85) |
| `--sv-bg-elevated` | `#f1f5f9` | `#DBD8F1` | mix(p,'#f4f4f9',.82) |
| `--sv-bg-overlay` | `#e2e8f0` | `#D0CDE5` | dk(sfH,.05) |
| `--sv-bg-hover` | `#e8ecf2` | `#C5C2D9` | dk(sfH,.10) |
| `--sv-bg-active` | `#dde3ed` | `#B2B0CB` | dk(bd,.10) |
| `--sv-bg-glass` | `rgba(255,255,255,.85)` | `rgba(227,224,245,.85)` | |
| `--sv-bg-glass-strong` | `rgba(255,255,255,.95)` | `rgba(227,224,245,.95)` | |
| `--sv-surface-0` | `#f8fafc` | `#E0DEF0` | |
| `--sv-surface-1` | `#ffffff` | `#E3E0F5` | |
| `--sv-surface-2` | `#f1f5f9` | `#DBD8F1` | |
| `--sv-surface-3` | `#e2e8f0` | `#D0CDE5` | |
| `--sv-surface-4` | `#dde3ed` | `#C5C2D9` | |
| `--sv-border` | `rgba(0,0,0,.06)` | `rgba(106,90,205,.10)` | |
| `--sv-border-light` | `rgba(0,0,0,.04)` | `rgba(106,90,205,.06)` | |
| `--sv-border-hover` | `rgba(0,0,0,.12)` | `rgba(106,90,205,.20)` | |
| `--sv-border-active` | `rgba(0,0,0,.16)` | `rgba(106,90,205,.25)` | |

**文字色（亮色）:**

| 变量 | 旧值 | 新值 |
|------|------|------|
| `--sv-text-primary` | `#1a2332` | `#181820` |
| `--sv-text-secondary` | `#4a5568` | `#585868` |
| `--sv-text-muted` | `#6b7a8e` | `#888898` |
| `--sv-text-inverse` | `#ffffff` | `#E0DEF0` |
| `--sv-text-placeholder` | `#9aa5b4` | `#9A9AA7` |

**开关/对比/玻璃:**

| 变量 | 旧值 | 新值 |
|------|------|------|
| `--sv-switch-track-off` | `#cbd5e1` | `#C6C3E2` |
| `--sv-compare-slider-bg` | `#1a2332` | `#181820` |
| `--sv-compare-label-color` | `#1a2332` | `#181820` |
| `--sv-glass-bg` | `rgba(255,255,255,.8)` | `rgba(227,224,245,.8)` |
| `--sv-glass-border` | `rgba(0,0,0,.06)` | `rgba(106,90,205,.10)` |

---

### 步骤 3: 更新 favicon SVG

**文件:** `bin/integrated_app/templates/base.html`

| 行号 | 属性 | 旧颜色 | 新颜色 |
|------|------|--------|--------|
| 17 | favicon | `#9b8ec4` → `#b8a9d4` | `#6a5acd` → `#ff7f50` |
| 18 | apple-touch-icon | `#9b8ec4` → `#b8a9d4` | `#6a5acd` → `#ff7f50` |

URL 编码: `%239b8ec4` → `%236a5acd`, `%23b8a9d4` → `%23ff7f50`

---

### 步骤 4: 浏览器验证

1. 启动本地服务器 `python -m http.server 8765`
2. 浏览器访问 `http://localhost:8765`
3. 检查暗色/亮色主题切换
4. 验证视觉效果：导航栏、Hero 渐变、按钮、卡片、状态环、背景层次

---

## 不修改的内容

- 语义色 (success/warning/danger/info)
- 阴影结构（已引用变量，自动更新）
- JavaScript 逻辑（无硬编码颜色）
- 模板文件（除 base.html favicon）
- 翻译文件
- 文档文件

## 可访问性说明

- 暗色模式主按钮文字 `#171528` 在 `#6A5ACD` 背景上满足大文本对比度
- 亮色模式主按钮文字 `#ffffff` 在 `#6A5ACD` 背景上满足对比度
- 亮色模式背景高度染紫（`#E0DEF0`）是 genTheme 设计意图，创造沉浸式色彩体验
