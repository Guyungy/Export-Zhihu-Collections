# -*- coding: utf-8 -*-
"""获取知乎收藏夹信息的兼容模块。

.. deprecated::
    这个模块的功能已经合并进 :mod:`fetch_collections`，
    保留它是为了让旧脚本 ``from get_collections import ...`` 仍然可用。
    新代码请直接使用 ``fetch_collections`` 或 ``zhihu_export.collections``。
"""

from __future__ import annotations

import json
import logging
import warnings
from typing import Dict, List, Optional

from zhihu_export import http as http_mod
from zhihu_export.collections import fetch_mine_collections, parse_collection_id

warnings.warn(
    "get_collections 已并入 fetch_collections，此模块仅为向后兼容而保留",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "load_cookies",
    "get_collections_from_page",
    "get_all_collections",
    "save_collections_to_json",
    "process_open_collection_mode",
]


def load_cookies() -> Dict[str, str]:
    """读取 cookies.json，返回 ``{name: value}``。"""
    return http_mod.load_cookies()


def get_collections_from_page(page_num: int = 1, cookies=None, session=None):
    """抓取「我的收藏夹」第 N 页，返回 ``(列表, 是否有内容)``。"""
    from fetch_collections import get_collections_from_page as _impl

    return _impl(page_num, cookies, session)


def get_all_collections(cookies=None, session=None, max_pages: int = 50) -> List[Dict[str, str]]:
    """获取全部收藏夹。"""
    owns_session = session is None
    session = session or http_mod.create_session(cookies=cookies or {})
    try:
        return fetch_mine_collections(session, max_pages=max_pages)
    finally:
        if owns_session:
            session.close()


def save_collections_to_json(collections: List[Dict[str, str]], filename: str = "zhihuUrls.json") -> bool:
    """把收藏夹列表保存到 JSON 文件。"""
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(collections, f, ensure_ascii=False, indent=2)
        logging.info("收藏夹列表已保存到: %s", filename)
        print("收藏夹列表已保存到: %s" % filename)
        return True
    except Exception as exc:  # noqa: BLE001
        logging.error("保存文件失败: %s", exc)
        print("保存文件失败: %s" % exc)
        return False


def process_open_collection_mode(cookies: Optional[Dict[str, str]] = None) -> bool:
    """旧版 openCollection 流程：抓取收藏夹并生成 zhihuUrls.json。"""
    if cookies is None:
        cookies = load_cookies()

    print("开始从知乎页面获取收藏夹列表...")
    logging.info("开始从知乎页面获取收藏夹列表...")

    collections = get_all_collections(cookies)
    if not collections:
        print("未获取到任何收藏夹")
        return False

    print("总共获取到%d个收藏夹" % len(collections))
    success = save_collections_to_json(collections, "zhihuUrls.json")
    if success:
        print("收藏夹列表已写入 zhihuUrls.json")
        print("建议改用 fetch_collections.py，它会把结果直接写回 config.json")
    return success


if __name__ == "__main__":
    print("测试 get_collections 模块（兼容层）...")
    _cookies = load_cookies()
    print("加载 cookies: %s" % ("成功" if _cookies else "失败"))

    _items, _has_more = get_collections_from_page(1, _cookies)
    print("获取到 %d 个收藏夹，是否有更多: %s" % (len(_items), _has_more))
    for index, item in enumerate(_items[:3], 1):
        print("  %d. %s: %s" % (index, item["name"], parse_collection_id(item["url"])))
