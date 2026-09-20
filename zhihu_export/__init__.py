# -*- coding: utf-8 -*-
"""Export-Zhihu-Collections 的内部实现包。

对外仍然保留 ``main.py`` / ``fetch_collections.py`` / ``get_collections.py``
三个入口脚本，这里放的是它们共用的能力：

- :mod:`zhihu_export.config`    —— 配置加载与跨平台输出路径解析
- :mod:`zhihu_export.http`      —— 统一 UA / cookies / 自动重试 / 限流
- :mod:`zhihu_export.collections` —— 收藏夹列表与收藏夹条目抓取
- :mod:`zhihu_export.converter` —— HTML → Obsidian 风格 Markdown，含正文图片下载
- :mod:`zhihu_export.logging_utils` —— 日志初始化与强制刷新
"""

__version__ = "2.0.0"

__all__ = [
    "__version__",
    "config",
    "converter",
    "collections",
    "http",
    "logging_utils",
]
