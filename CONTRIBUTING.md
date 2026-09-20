# 贡献指南

欢迎提交 issue 与 PR。这个项目的目标很窄：**把「导出知乎收藏夹」这件事做到不容易坏**。
所以评审时的第一标准不是功能多，而是「失败时会不会留下坏状态」。

## 环境

```bash
git clone https://github.com/Guyungy/Export-Zhihu-Collections.git
cd Export-Zhihu-Collections
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest                                               # 应该 142 passed，且不联网
```

测试全部离线：用假会话（`test/helpers.py`）+ 页面样例（`test/fixtures/*.html`）。
**不要**为了写测试去联网抓知乎 —— 那会让测试随知乎改版而随机失败。

## 提交前

```bash
python -m compileall -q main.py fetch_collections.py get_collections.py utils.py zhihu_export tools
python tools/check_readme_anchors.py README.md CHANGELOG.md CONTRIBUTING.md   # 改了标题就顺手跑
pytest
```

三条都过再提 PR。CI 会在 Python 3.8 / 3.11 / 3.13 上跑同一套测试。

> 提到了 README 的标题？跑一下锚点自检。GitHub 的锚点会保留 emoji 后面的变体选择符
> （`## ⚙️ 配置说明` 的锚点是 `#️-配置说明`，`#` 后面有个不可见字符），少写一个不可见字符，
> 目录链接就会静默失效，肉眼完全看不出来。`tools/check_readme_anchors.py` 专门抓这个。

## 代码约定

1. **导入不能有副作用。** 不要在模块级建目录、读 `cookies.json`、发请求。
2. **公共函数名向后兼容。** `main.load_config` / `get_output_path` / `build_reserved_file_path` /
   `process_single_collection` 等名字被外部脚本依赖，改实现可以，改名不行。
3. **抓取失败不要写文件。** 失败返回 `FETCH_FAILED` 并留 `debug/*.html`；
   如果写了占位 `.md`，这篇会被「已下载」判定拦住，永远不再重试。
4. **单点失败必须隔离。** 一张图、一篇文章失败都不能中断整夹导出。
5. **图片文件名只有一个来源。** `ImageDownloader._resolve_name` 决定落盘名，
   Markdown 引用同一个名字，两边走同一函数。
6. **错误信息要能直接照做。** 401 / 403 / 404 交给 `describe_http_error` / `describe_api_error`
   翻译成「怎么修」，不要只抛状态码。
7. **新增内容类型**：在 `zhihu_export/collections.py` 的 `SUPPORTED_TYPES` 与 `TYPE_LABELS` 里登记，
   在 `zhihu_export/api.py` 的 `build_api_url` 里加对应端点，两处都要有测试。

## 新增依赖

- 运行依赖进 `requirements.txt`（带下界与大版本上界），同时同步 `pyproject.toml` 的 `dependencies`。
- 测试依赖进 `requirements-dev.txt`。
- 优先用标准库能解决的东西。

## 报 bug

请在 <https://github.com/Guyungy/Export-Zhihu-Collections/issues> 提，附上：

1. 完整报错（终端输出或 `logs/debug_*.log` 里的报错段落）
2. 出问题的 URL（如果内容不适合公开，说清是「回答 / 专栏 / 想法」中的哪类即可）
3. 系统与 Python 版本
4. 复现用的配置片段（**打码掉 cookies 与个人路径**）

> ⚠️ 提交前请务必确认：**不要贴 `cookies.json` 或任何 cookie 值**。
> 那是账号登录凭证，贴出来等于把账号交出去。

## 不要提交

- `cookies.json`
- `downloads/`、`logs/`、`debug/` 等导出产物
- 含个人收藏夹清单的 `config.json`（模板放 `config.example.json`）

`.gitignore` 已经覆盖这些，但请自己再确认一次 `git status`。
