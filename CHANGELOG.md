# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。
日期为提交日期，条目对应真实提交，不做修饰。

---

## [3.0.0] - 2026-09-20

一次以「可维护性 + 抗失败」为目标的重构。对外行为向后兼容（`main.py` 的公共函数名、配置文件字段都没变）。

### 架构

- 抽出 `zhihu_export/` 包：`config` / `http` / `collections` / `converter` / `logging_utils` / `api`，
  `main.py`、`fetch_collections.py`、`get_collections.py` 变薄，消除三处重复实现。
- `import main` 不再有副作用（旧版本导入即建目录、读 cookies）。所有初始化收进 `main()`。
- 历史自检脚本（19 个）移入 `test/legacy/`，不再参与 pytest 收集；诊断脚本移入 `tools/`。
- `main.py` 收敛为 CLI：`--list` / `--only` / `--workers` / `--delay` / `--output` / `--dry-run` / `--retries`。

### 新增

- `zhihu_export/api.py`：**正文 API 兜底**。页面被 403、404 或改版挡住时，改调
  `/api/v4/{answers,articles,pins}/{id}` 取正文；想法（pin）的分块结构会拼回 HTML。
- `describe_api_error()`：把知乎的机器码翻译成可操作提示 ——
  `need_login`、`ERR_LOGIN_TICKET_EXPIRED`、风控 `code 10003`。
- `fetch_page_with_retries()`：应用层重试，覆盖「流式读取中途断流」这类 requests 层重试抓不到的场景。
- 逐篇结果记录正文来源（`page` / `api`），结尾汇总打印 API 兜底篇数与重试次数。
- 懒加载图片还原：把 `data-original` 里的真实图片地址提升为 `src`。
- 新增配置项：`pageTimeout` / `longPageTimeout` / `fetchRetries` / `apiFallback`。
- `pyproject.toml`（打包元数据 + pytest 配置）、`requirements-dev.txt`、GitHub Actions CI。

### 修复

- 单张图片下载失败不再中断整篇导出，降级为远程链接并计入 `image_failures`。
- 抓取失败**不再写占位 `.md`** —— 否则会被「已下载」判定拦住，永远不再重试。改为存 `debug/*.html`。
- 补齐重试（429 / 5xx / 连接重置）、全局请求限速、连接复用；4xx/410 不做无意义重试。
- 「想法」类型条目现在可以导出；不支持的类型显式记录原因，不再静默丢弃。
- 401 不再只报「没获取到」，翻译为「登录票据过期，请重新导出 cookies」并醒目提示一次。
- Windows 路径跑到 macOS/Linux 上不再生成名为 `D:` 的怪目录，会回退到 `downloads/`；
  `outputPath` 支持按系统分别配置。
- 超长中文标题按 UTF-8 字节截断，避免「文件名过长」导致整篇失败。
- 请求头补齐现代浏览器字段；`Accept-Encoding` 按本机实际解码能力协商，
  不声明解不开的 `br` / `zstd`（否则正文到手是乱码）。
- `.gitignore` 原 `*.json` 通配会吞掉新配置文件，改写为精确规则。

### 安全与仓库卫生

- `cookies.json` 取消版本跟踪并加入 `.gitignore`，改提供 `cookies.example.json`。
- 依赖加上下界（并锁定大版本上界），避免上游破坏性变更直接打进来。

### 测试

- 新增 136 项**离线** pytest（假会话 + 4 份知乎页面样例）：不联网、不需要 cookies、0.2 秒跑完。
  覆盖配置解析、文件名边界、收藏夹分页、401 提示、图片缓存与降级、
  API 兜底 URL 解析与错误翻译、403 → API 回退、流式读取、端到端整夹导出。
- 真实响应固化：cookies 过期时抓到的 `need_login` / `ERR_LOGIN_TICKET_EXPIRED` / `code 10003`
  都变成了回归用例，而不是写在注释里的口头结论。

### 文档

- README 重写：徽章、目录、特性表、真实终端输出、导出效果、配置与 CLI 参数表、
  Mermaid 流程图（含兜底分支）、项目结构、测试与 CI、路线图、FAQ、隐私说明、致谢。
- 新增 `CHANGELOG.md`（本文件）与 `CONTRIBUTING.md`。

### 说明

- 正文 API 兜底与专栏流式读取两条思路，来自衍生分支
  [JasonJarvan/Zhihu-Collections-MCP](https://github.com/JasonJarvan/Zhihu-Collections-MCP)。
  本版本在本仓库架构下重写，并修掉了原始实现里的三个问题（先 `raise_for_status` 丢掉错误体、
  懒加载图被 DOM 清理误删、声明了本机解不开的压缩编码）。**未引入其 MCP Server 部分。**

---

## [2.1.0] - 2026-03-10

- 优化交互式多线程体验（`cf8be40`）。
- 补充 cookies 模板与说明文档（`e480ca0`）。
- README 增强。

## [2.0.0] - 2026-04-06

- 补充 `LICENSE`（GPL-3.0，`8fa07a1`、`fd9d154`）。
- 依赖整理（`ca6a207`）。

## [1.0.0] - 2025-01-15

- 初始版本（`531fe53`）：收藏夹批量导出、多线程下载、跨平台路径处理、
  正文图片本地化、Obsidian 内嵌图片语法、运行日志与调试 HTML 留档。
