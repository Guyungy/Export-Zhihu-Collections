# 测试说明

本目录是 Export-Zhihu-Collections 的测试集合，**全部离线运行**：不需要网络、不需要 `cookies.json`，
知乎页面用 `fixtures/` 里的样例 HTML，HTTP 用假会话（`FakeSession`）替代。

## 快速开始

```bash
# 在项目根目录执行
pip install -r requirements-dev.txt
pytest
```

## 目录结构

```text
test/
├── conftest.py          # pytest fixture（样例 HTML、假会话）
├── helpers.py           # FakeResponse / FakeSession / fixture 读取工具
├── fixtures/            # 知乎页面样例 HTML
│   ├── answer_page.html              # 回答页（含图片、链接卡片、mailto、脚注回链）
│   ├── post_page.html                # 专栏页
│   ├── mine_collections_page.html    # 我的收藏夹（标准结构）
│   └── mine_collections_page_v2.html # 我的收藏夹（改版后的结构，用于验证兜底解析）
├── test_config.py       # 配置加载与跨平台路径解析
├── test_utils.py        # 文件名清理与截断
├── test_collections.py  # 收藏夹清单 / 条目抓取与解析
├── test_converter.py    # HTML → Markdown 与图片下载
├── test_main_integration.py  # main.py 端到端流程与 CLI
└── legacy/              # 历史自检脚本，pytest 不收集
```

## 各文件覆盖范围

### test_config.py

- `normalize_os` / `is_windows_path` 等系统识别逻辑
- `parse_output_path`：`~` 展开、相对路径转绝对路径、空值处理
- `resolve_output_path`：单路径 / 按系统映射 / Windows 路径在 macOS 上被拒 / 命令行覆盖
- `downloadWorkers` / `imageWorkers` / `requestDelay` 的边界夹取
- `load_config`：正常、缺文件、坏 JSON、旧版 `zhihuUrls.json` 回退

### test_utils.py

- 文件名非法字符替换、全角化 `?` / `:`、空白合并、空标题兜底
- 长中文标题按 UTF-8 字节截断（不会截出半个汉字）

### test_collections.py

- 收藏夹 ID 解析（链接 / 带查询串 / 裸 ID）
- 条目标题解析：回答取问题标题、专栏取文章标题、想法取摘要
- 分页抓取、`max_items` 限制、不支持类型（视频）跳过
- 接口异常时返回已抓到的部分，不抛异常
- 401 / 403 / 404 的可操作提示文案
- 「我的收藏夹」页面：标准选择器解析、改版后链接扫描兜底、翻页到底判定

### test_converter.py

- 图片下载、缓存命中、扩展名推断、同名不同图加摘要后缀、失败不抛异常
- `data:` 图片跳过、并发预取去重
- 图片走 Obsidian 内嵌语法 `![[...]]`，失败时保留远程链接
- 链接卡片使用卡片标题、`mailto:` 链接降级为纯文本、脚注回链不产生列表符号
- 标题使用 ATX 风格（`## `）

### test_main_integration.py

- `is_article_already_downloaded`：空文件视为未下载
- 并发占位文件名去重、输出目录推导、`--only` 过滤、CLI 参数默认值
- 端到端：整夹导出（含图片落盘）、重复运行跳过、专栏 URL 路由到专栏解析器、
  失败不写占位文件、`--skip-images`、处理日志 JSON
- CLI：`--list` 输出、`openCollection` 模式提示、空配置返回码

## 关于 legacy/

`legacy/` 里是早期调试阶段留下的自检脚本（例如 `test_main_fixes.py`、`test_syntax.py`），
它们通过**读取源码文本做模式匹配**来验证当时的具体修复，与实现强耦合，重构后会失效。

这些脚本已由 `test/` 下基于行为的 pytest 用例取代，仅作历史记录保留，
通过 `pyproject.toml` 里的 `norecursedirs` 排除在 pytest 收集之外，需要时可以手动执行。

## 贡献测试

1. 新增用例放在 `test/test_*.py`，与实现的模块一一对应
2. 需要页面结构时，往 `test/fixtures/` 加样例 HTML，而不是访问真实站点
3. 需要 HTTP 时使用 `helpers.FakeSession`，保持测试离线、可重复
4. 断言行为（返回值、落盘文件、日志文案），不要断言源码文本
