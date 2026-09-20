# -*- coding: utf-8 -*-
"""诊断工具：把「我的收藏夹」原始页面抓下来，分析真实 HTML 结构。

用法::

    python tools/debug_page.py                  # 抓第 1 页并分析
    python tools/debug_page.py --page 2         # 抓第 2 页
    python tools/debug_page.py --save out.html  # 同时保存原始 HTML

知乎改版后 ``fetch_collections.py`` 抓不到收藏夹时，用这个脚本确认新的 class 名，
再回来调整 ``zhihu_export/collections.py`` 里的解析策略。
"""

from __future__ import annotations

import argparse
import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bs4 import BeautifulSoup

from zhihu_export import http as http_mod
from zhihu_export.collections import MINE_PAGE_URL, _parse_mine_page_html

CANDIDATE_CLASSES = (
    "SelfCollectionItem",
    "CollectionItem",
    "Collection-item",
    "collection-item",
    "self-collection-item",
    "UserCollection",
    "MyCollection",
)


def analyze(html: str) -> None:
    """打印页面里与收藏夹相关的结构信息。"""
    soup = BeautifulSoup(html, "lxml")

    print("\n=== class 命中情况 ===")
    for class_name in CANDIDATE_CLASSES:
        found = soup.find_all(class_=class_name)
        if found:
            print("✓ %-24s %d 个" % (class_name, len(found)))

    print("\n=== 收藏夹链接 ===")
    links = [a for a in soup.find_all("a", href=True) if "/collection/" in a["href"]]
    print("共 %d 个 /collection/ 链接" % len(links))
    for link in links[:5]:
        print("  - %s | 文本=%r | class=%s" % (link["href"], link.get_text(strip=True), link.get("class")))

    print("\n=== 当前解析函数的结果 ===")
    parsed = _parse_mine_page_html(html)
    print("解析出 %d 个收藏夹" % len(parsed))
    for index, item in enumerate(parsed[:5], 1):
        print("  %d. %s -> %s" % (index, item["name"], item["url"]))

    if "登录" in html or "login" in html.lower():
        print("\n⚠️  页面包含登录相关内容，cookies 可能已失效")


def main() -> int:
    parser = argparse.ArgumentParser(description="分析知乎「我的收藏夹」页面结构")
    parser.add_argument("--page", type=int, default=1, help="页码，默认 1")
    parser.add_argument("--save", help="把原始 HTML 保存到指定文件")
    args = parser.parse_args()

    session = http_mod.create_session(cookies=http_mod.load_cookies())
    url = MINE_PAGE_URL.format(page=args.page)

    print("正在获取页面: %s" % url)
    try:
        response = session.get(url, timeout=20)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print("✗ 请求失败: %s" % exc)
        return 1

    print("HTTP %s，响应长度 %d" % (response.status_code, len(response.text)))

    if args.save:
        target = pathlib.Path(args.save)
        target.write_text(response.text, encoding="utf-8")
        print("原始 HTML 已保存到: %s" % target)

    analyze(response.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
