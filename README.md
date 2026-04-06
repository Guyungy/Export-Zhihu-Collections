# Export-Zhihu-Collections

把知乎收藏夹导出为本地 Markdown 文件。

## 功能

- 支持批量导出多个收藏夹
- 支持公开收藏夹和带 `cookies.json` 的私密收藏夹
- 支持自定义输出目录
- 自动跳过已存在文件
- 下载正文图片并转换为 Markdown

## 安装

```bash
pip install -r requirements.txt
```

## 配置

在项目根目录准备 `config.json`：

```json
{
  "zhihuUrls": [
    {
      "name": "示例收藏夹",
      "url": "https://www.zhihu.com/collection/123456789"
    }
  ],
  "outputPath": "",
  "os": "",
  "openCollection": false
}
```

字段说明：

- `zhihuUrls`: 要导出的收藏夹列表
- `outputPath`: 输出目录，留空则使用项目下的 `downloads/`
- `os`: 操作系统类型，留空则自动判断
- `openCollection`: 设为 `true` 时，用于先抓取“我的收藏夹”列表

## 运行

导出收藏夹：

```bash
python main.py
```

先抓取“我的收藏夹”列表，再写回 `config.json`：

```bash
python fetch_collections.py
```

## 私密收藏夹

如果要导出私密收藏夹，请在项目根目录放置 `cookies.json`：

```json
[
  { "name": "cookie_name", "value": "cookie_value" }
]
```

没有 `cookies.json` 也能运行，但私密内容通常无法获取。

## 输出目录

默认输出到：

```text
downloads/
```

典型结构：

```text
downloads/
├─ 收藏夹名称/
│  ├─ 文章1.md
│  └─ assets/
├─ logs/
└─ debug/
```

## 说明

- 若知乎页面结构变化，部分内容可能抓取失败
- 失败时可查看 `downloads/logs/` 和 `downloads/debug/`
- 本项目仅用于个人学习、备份和整理
