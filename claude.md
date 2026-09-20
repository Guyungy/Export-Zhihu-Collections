# CLAUDE.md

此文件为 Claude Code (claude.ai/code) 在此仓库中工作时提供指导。

## 重要规则

- **始终使用中文回答**
- 在处理此项目时，所有交流都应使用中文

## 项目概述

Export-Zhihu-Collections 把知乎收藏夹导出为本地 Markdown（Obsidian 友好）：

- 通过知乎 API 分页拉取收藏夹条目，支持回答 / 专栏 / 想法
- 用多套 DOM 选择器 + 智能检测提取正文，兼容知乎改版
- 页面被 403 / 改版挡住时，自动改走知乎 OpenAPI 取正文（`zhihu_export/api.py`）
- 正文图片并发下载到 `assets/`，正文改写为 `![[文件名]]`
- 支持公开与私密收藏夹、批量导出、断点续传、详细日志与调试留档

## 架构

```text
main.py                → 导出入口（CLI，argparse）
fetch_collections.py   → 抓取「我的收藏夹」并写回配置（CLI）
get_collections.py     → 兼容层（旧脚本 import 用，内部转调 fetch_collections）
utils.py               → 文件名清理与按字节截断
zhihu_export/          → 内部实现包
  config.py            → 配置加载、校验、跨平台路径解析、并发与超时参数边界
  http.py              → Session + 浏览器请求头 + 重试 + cookies + RateLimiter
  collections.py       → 收藏夹清单 / 条目抓取、类型解析、错误翻译
  api.py               → 正文 API 兜底（answers / articles / pins）+ 错误翻译
  converter.py         → ImageDownloader + ObsidianStyleConverter + DOM 清理
  logging_utils.py     → 日志初始化与强制刷新
tools/                 → 诊断脚本（analyze_issue.py / debug_page.py）
test/                  → pytest 测试（离线） + fixtures + legacy 历史脚本
```

### 关键约定

1. **导入 `main` 不能有副作用**：旧版本在 import 时会建目录、读 cookies，现在所有初始化都在 `main()` 里做。新增代码不要退回模块级副作用。
2. **公共函数名保持向后兼容**：`main.load_config` / `get_output_path` / `get_debug_path` / `build_reserved_file_path` / `process_single_collection` / `get_single_answer_content` 等名字被外部脚本依赖，改内部实现可以，改名不行。
3. **正文失败返回 `main.FETCH_FAILED`（-1）**，且**不写占位 `.md`** —— 否则失败的文章会被 `is_article_already_downloaded` 误判为已下载，永不再重试。
4. **图片文件名映射必须自洽**：`ImageDownloader._resolve_name` 决定落盘名，Markdown 里引用同一个名字，两边都走同一函数。
5. **DTO 用 `CollectionItem`**：标题解析规则集中在 `_item_title`，新增内容类型只需在 `SUPPORTED_TYPES` 与 `TYPE_LABELS` 里登记。
6. **错误提示要可操作**：HTTP 层由 `describe_http_error` 翻译，API 层由 `api.describe_api_error` 翻译成「怎么修」，不要只抛状态码或机器码。
7. **正文来源要能追溯**：`_try_api_fallback` 成功后调 `_mark_content_source("api")`，
   `download_single_article` 用 `_consume_content_source()` 取回写进 `logs/*.json` 的 `source` 字段。
   这是 **thread-local**，多线程下不能改成全局变量。
8. **`.gitignore` 不要用 `*.json` 这类宽通配**：会吞掉新增的配置文件。
9. **`cookies.json` 永不入库**：历史上曾误提交过（见 git 历史 `eaac68d`），别再犯。

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
  "requestDelay": 0.4,
  "pageTimeout": 30,
  "longPageTimeout": 120,
  "fetchRetries": 3,
  "apiFallback": true
}
```

`outputPath` 支持字符串或 `{ "windows": ..., "macos": ..., "linux": ... }` 映射；
Windows 路径跑到非 Windows 系统上会被识别并回退到 `downloads/`（不会生成 `D:` 目录）。

`os` 留空表示自动检测；`downloadWorkers` / `imageWorkers` 会被夹到 1–16，`requestDelay` 夹到 0–10，
`pageTimeout` / `longPageTimeout` 夹到 1–600，`fetchRetries` 夹到 0–10。
完整样例见 `config_examples.json`。

## 正文抓取路线（改这块前先读）

`get_single_answer_content` / `get_single_post_content` 都是「先页面、后 API」：

1. **页面**：`fetch_page_with_retries` → 多套选择器 → `smart_content_detection` 兜底
   - 专栏走 `stream=True` + `longPageTimeout`（长正文按块读，读超时单独放宽）
   - `_NON_RETRYABLE_STATUS`（400/401/403/404/410）不重试，直接转兜底 —— 重试 403 纯浪费时间
2. **API 兜底**：`api.fetch_content_via_api` → `answers` / `articles` / `pins`
   - **先读 JSON 再判状态码**。知乎把 `need_login` / `ERR_LOGIN_TICKET_EXPIRED` / 风控 `code 10003`
     这些都放在 4xx 的响应体里，直接 `raise_for_status()` 会把线索丢掉。
   - 拿回的是**正文片段**，`_wrap_api_content` 清洗后只取 `body` 子节点再包一层 `html_template`，
     不要嵌套 `<html>` 标签。

**坑**：知乎正文的图片常写成
`<img src="data:image/svg+xml;..." data-original="https://picx.zhimg.com/...">`。
`sanitize_content` 会删掉 `data:` 占位图，所以必须先跑 `promote_lazy_images` 把真实地址提升到 `src`，
否则 API 兜底拿到的正文会**一张配图都不剩**。

实测（2026-09-20，cookies 已过期时）：

| 端点 | 结果 |
| --- | --- |
| 网页 `/question/*/answer/*`、`zhuanlan.zhihu.com/p/*` | 403，HTML 空壳 |
| `/api/v4/answers/{id}?include=content` | 403 + `{"error":{"need_login":true}}` |
| `/api/v4/pins/{id}` | 401 + `ERR_LOGIN_TICKET_EXPIRED` |
| `/api/v4/articles/{id}` | 403 + `code 10003`（风控） |

即：**换请求头不能绕过 403，兜底接口也需要有效登录态**。这三条实测响应都写进了
`test/test_api.py::TestDescribeApiError` 做回归。

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
- 逐篇结果：`<输出目录>/logs/*.json`（正常下载 / 跳过 / 失败 + 失败原因 + **正文来源 `source`**）
- 解析失败的原始页面：`<输出目录>/debug/debug_answer_*.html`、`debug_post_*.html`
- 单篇失败的判断顺序：先看 `source` 是不是 `api`（走了兜底说明页面被拦），再看失败原因里
  有没有「登录票据已失效 / need_login / code 10003」——这三种都是 cookies 的问题，不是代码的问题。
