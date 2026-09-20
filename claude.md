# CLAUDE.md

此文件为 Claude Code (claude.ai/code) 在此仓库中工作时提供指导。

## 重要规则

- **始终使用中文回答**
- 在处理此项目时，所有交流都应使用中文

## 项目概述

Export-Zhihu-Collections 把知乎收藏夹导出为本地 Markdown（Obsidian 友好）：

- 通过知乎 API 分页拉取收藏夹条目，支持回答 / 专栏 / 想法
- 用多套 DOM 选择器 + 智能检测提取正文，兼容知乎改版
- 正文图片并发下载到 `assets/`，正文改写为 `![[文件名]]`
- 支持公开与私密收藏夹、批量导出、断点续传、详细日志与调试留档

## 架构

```text
main.py                → 导出入口（CLI，argparse）
fetch_collections.py   → 抓取「我的收藏夹」并写回配置（CLI）
get_collections.py     → 兼容层（旧脚本 import 用，内部转调 fetch_collections）
utils.py               → 文件名清理与按字节截断
zhihu_export/          → 内部实现包
  config.py            → 配置加载、校验、跨平台路径解析、并发参数边界
  http.py              → Session + 重试 + cookies + RateLimiter
  collections.py       → 收藏夹清单 / 条目抓取、类型解析、错误翻译
  converter.py         → ImageDownloader + ObsidianStyleConverter + DOM 清理
  logging_utils.py     → 日志初始化与强制刷新
tools/                 → 诊断脚本（analyze_issue.py / debug_page.py）
test/                  → pytest 测试（离线） + fixtures + legacy 历史脚本
```

### 关键约定

1. **导入 `main` 不能有副作用**：旧版本在 import 时会建目录、读 cookies，现在所有初始化都在 `main()` 里做。新增代码不要退回模块级副作用。
2. **公共函数名保持向后兼容**：`main.load_config` / `get_output_path` / `get_debug_path` / `build_reserved_file_path` / `process_single_collection` 等名字被外部脚本依赖，改内部实现可以，改名不行。
3. **正文失败返回 `main.FETCH_FAILED`（-1）**，且**不写占位 `.md`** —— 否则失败的文章会被 `is_article_already_downloaded` 误判为已下载，永不再重试。
4. **图片文件名映射必须自洽**：`ImageDownloader._resolve_name` 决定落盘名，Markdown 里引用同一个名字，两边都走同一函数。
5. **DTO 用 `CollectionItem`**：标题解析规则集中在 `_item_title`，新增内容类型只需在 `SUPPORTED_TYPES` 与 `TYPE_LABELS` 里登记。
6. **错误提示要可操作**：401 / 403 / 404 由 `describe_http_error` 翻译成「怎么修」，不要只抛原始状态码。

## 常用命令

```bash
# 安装依赖
pip install -r requirements.txt

# 跑测试（离线，无需 cookies）
pytest

# 导出（仅列清单 / 试跑 1 条 / 只导某几个）
python main.py --list
python main.py --dry-run
python main.py --only 技术-效率工具 --workers 4 --delay 1.0

# 抓取「我的收藏夹」写回 config.json
python fetch_collections.py --dry-run

# 诊断
python tools/analyze_issue.py
python tools/debug_page.py --page 1 --save /tmp/mine.html
```

## 配置

```json
{
  "zhihuUrls": [{ "name": "收藏夹名", "url": "https://www.zhihu.com/collection/123456789" }],
  "outputPath": "",
  "os": "",
  "openCollection": false,
  "downloadWorkers": 6,
  "imageWorkers": 4,
  "requestDelay": 0.4
}
```

`outputPath` 支持字符串或 `{ "windows": ..., "macos": ..., "linux": ... }` 映射；
Windows 路径跑到非 Windows 系统上会被识别并回退到 `downloads/`（不会生成 `D:` 目录）。

`os` 留空表示自动检测；`downloadWorkers` / `imageWorkers` 会被夹到 1–16，`requestDelay` 夹到 0–10。
完整样例见 `config_examples.json`。

## 依赖

- `requests` —— HTTP 会话（`zhihu_export/http.py`，含 urllib3 Retry）
- `beautifulsoup4` + `lxml` —— HTML 解析
- `markdownify>=1.1,<2` —— HTML → Markdown；`ObsidianStyleConverter` 依赖其
  `convert_a/convert_li/convert_img(el, text, parent_tags)` 签名，升级大版本前先跑测试
- `tqdm` —— 进度条
- `pytest` —— 测试（dev 依赖）

## 认证

私密收藏夹需要项目根目录的 `cookies.json`（浏览器导出的 JSON 数组，或 `{name: value}` 对象）。
文件已被 `.gitignore` 忽略，**不要提交**。缺少或过期时程序会提示重新导出；
`tools/analyze_issue.py` 可一键判断问题出在 cookies、接口还是页面结构。

## 调试与排查

- 运行日志：`<输出目录>/logs/debug_*.log`（DEBUG 级）
- 逐篇结果：`<输出目录>/logs/*.json`（正常下载 / 跳过 / 失败 + 失败原因）
- 解析失败的原始页面：`<输出目录>/debug/debug_answer_*.html`、`debug_post_*.html`
