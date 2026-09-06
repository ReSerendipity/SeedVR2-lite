"""应用生命周期后台任务包。

app_server.lifespan 的周期性后台循环收敛于此（后端设计评估 2026-09-06：
lifespan 装配职责过重，内联闭包难以单测）。本包只承载「循环体」，
依赖经参数注入；app_server 保留装配与 app.state 挂载职责。

模块清单见 background_tasks.py。
"""
