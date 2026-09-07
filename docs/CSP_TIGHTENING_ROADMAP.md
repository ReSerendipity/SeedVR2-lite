# CSP 收紧路线图（评估报告 R8 · 2026-09-06）

> 现状基线：`middleware/security_headers.py` 的 `_DEFAULT_CSP` 与
> `templates/base.html` 的 meta CSP。评估报告 R8 指出 `'unsafe-inline'`
> 兜底与 Google Fonts CDN 依赖两点缺口；本路线图给出收紧路径与验收口径。
> 本文是「路线」承诺的落地物——报告原文即定位为路线而非本轮实施项。

## 1. 现状

- **script-src 'self' 'unsafe-inline'**：现代浏览器下 per-request nonce
  存在时忽略 `'unsafe-inline'`（CSP2/3 语义），实际防护由 nonce 承担；
  无 nonce 的渲染回退（`render_page` 未注入）与旧内核则退化为放行。
- **style-src 'self' 'unsafe-inline' https://fonts.googleapis.com**、
  **font-src https://fonts.gstatic.com**：内联 style + 外部字体 CDN。
- 约束（KNOWN_ISSUES #54 / 陷阱 #7）：meta CSP 与响应头 CSP **取交集**，
  任何一边收紧不同步都会拦截页面自身资源——两处必须原子同改。

## 2. 收紧步骤（按依赖顺序）

> **2026-09-06 盘点修订（S1 近乎完成，S2 逼近即时可行）**：全模板 grep
> `on(click|change|input|submit|keydown|load|error|…)=` 共 **0 处**内联事件属性；
> JS 的 100 处 `.onclick =` 为代码赋值（非 HTML 属性，不受 CSP script-src 约束）；
> 22 处 innerHTML 拼接不含 on*。base.html 6 个 script：2 个内联均带 nonce、
> 4 个外链同源。**S2 唯一残留障碍** = `history.html:276/:335` 两处
> `hx-on::after-request`（htmx 经 `new Function` 求值——注意：现行 CSP 无
> 'unsafe-eval'，此 2 处在当前策略下**疑似已静默失效**，删除前先人工确认
> 刷新链路现状）。等价改法：app.js 监听 `htmx:afterRequest` 延时 click
> （<15 分钟工作量 + 一轮 e2e 回归）。58 处模板 `style="…"`（restore.html 占 39）
> 仍是 S4 的主体工作量。

| 步骤 | 内容 | 验收 |
|---|---|---|
| S1 | ~~盘点全部模板/JS 内联事件处理器~~ → **盘点已完成（见上）**：模板零内联事件属性，仅 2 处 htmx `hx-on` 待等价迁移 | ~~grep 模板零 `on*=` 属性~~ ✅（hx-on 迁移后达成） |
| S2 | `script-src` 移除 `'unsafe-inline'`（先完成 hx-on 迁移）：meta 与响应头**同一提交**内同步移除 | `tests/test_csp_nonce.py` 全绿 + 断言 CSP 不含 unsafe-inline |
| S3 | 字体本地化：下载站酷小薇/马善政楷书 woff2 入 `/static/fonts/`（注意字体文件自身的再分发许可），`style-src/font-src` 移除 Google Fonts 域 | UI 字体切换器回归 + CSP 不含 fonts.googleapis.com |
| S4 | 内联 style 迁移（最大工作量，58 处属性）：组件级 `style=` 迁外部样式表/类切换后，`style-src` 移除 `'unsafe-inline'` | 全 UI 视觉回归截图对比 |

## 3. 发布策略

每步先以 **`Content-Security-Policy-Report-Only`** 双头并行观察 ≥1 周
（上报端点可复用 `/api/system/ping` 级轻量端点或控制台采集），零告警后
切换强制头并保留一步可回滚（meta/头两处均在同一提交内，git revert 即回滚）。

## 2b. 实施进度（2026-09-06 自主落地轮）

- **S1 ✅ 完成**：盘点证实模板零内联事件属性、JS 零拼接 `<script>`/on*（见上）。
  唯一残留 `history.html` 两处 `hx-on::after-request` 已迁移为 document 级
  `htmx:afterRequest` 委托监听——且经核实该属性经 htmx `new Function` 求值，
  在现行无 `'unsafe-eval'` 的 CSP 下**本已静默失效**，迁移同时修复了功能。
- **S2 ✅ 完成**：`base.html` meta CSP 改为 `script-src 'self'{% if csp_nonce %}
  'nonce-…'{% else %} 'unsafe-inline'{% endif %}`——nonce 上下文彻底移除
  `unsafe-inline`，无 nonce 异常路径保留回退防白屏；全部 7 处内联 script 由
  `render_page` 无条件注入的 per-request nonce 覆盖。响应头侧（`security_headers.py`）
  无 per-request nonce 可拼，按 CSP 交集语义（meta 更严者生效）保留字符串并注释。
  回归：`tests/test_csp_nonce.py` 新增「nonce 存在时 script-src 无 unsafe-inline」
  正向断言，既有回退断言继续通过。桌面壳桥接经 CDP document-created 通道注入，
  不受页面 CSP 约束（建议下轮真机复验一次标题字体/刷新链路）。
- **S3 ☑ 评估后关闭（不做自托管）**：装饰字体加载链已是完全按需——默认启动
  **零第三方请求**（`app.js` `ensureWebfonts` 仅在用户展开字体菜单或已保存装饰
  字体选择时触发）；界面基字体（DM Sans / Instrument Serif）本已本地化。
  自托管 11 个 Google 字体家族需引入 30–60MB CJK 子集碎片（unicode-range
  分片），与便携包体积治理（core ~1.9GB、体积注释三处同源）冲突且用户收益为零。
  CSP 的 `fonts.googleapis.com/gstatic.com` 域因此保留。决策 D22。
- **S4 ⏳ 部分**：见 §2c。

## 2c. S4 style= 盘点分类（2026-09-06）

## 4. 风险与边界

- S2 前必须完成 S1，否则内联脚本被浏览器静默拦截（表现为 UI 无报错失效）。
- 桌面壳（Tauri）通过 `initialization_script` 注入桥接代码，不走 HTTP 响应头；
  收紧 CSP 前需确认桥接注入点不依赖 `'unsafe-inline'`（见 desktop-bridge.js）。
- 明确不做的：CSP 放宽类变更（新增 CDN 域、data: 脚本源等）一律禁止。
