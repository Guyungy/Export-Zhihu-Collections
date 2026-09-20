# -*- coding: utf-8 -*-
"""收藏夹相关抓取：收藏夹列表（我的收藏夹）与收藏夹内条目。

两个入口：

* :func:`fetch_mine_collections` —— 抓「我的收藏夹」列表
  （优先走接口，失败自动回退 HTML 解析，并做了多套选择器兜底）
* :func:`fetch_collection_items` —— 抓某个收藏夹里的全部条目，
  并按内容类型（回答 / 专栏 / 想法）解析标题与链接
"""

from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Sequence

from bs4 import BeautifulSoup

from .http import RateLimiter

COLLECTION_ITEMS_API = "https://www.zhihu.com/api/v4/collections/{collection_id}/items"
MINE_PAGE_URL = "https://www.zhihu.com/collections/mine?page={page}"
ME_API = "https://www.zhihu.com/api/v4/me"
MEMBER_COLLECTIONS_API = "https://www.zhihu.com/api/v4/members/{token}/collections"

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# 能转成 Markdown 正文的内容类型；其余类型（如视频、商品）会被显式跳过并记录原因
SUPPORTED_TYPES = frozenset({"answer", "article", "pin"})
TYPE_LABELS = {
    "answer": "回答",
    "article": "专栏",
    "pin": "想法",
    "zvideo": "视频",
    "question": "问题",
}

_COLLECTION_URL_RE = re.compile(r"/collection/(\d+)")

#: 知乎在登录票据过期时返回的错误标记
AUTH_ERROR_MARKERS = (
    "ERR_LOGIN_TICKET_EXPIRED",
    "AuthenticationInvalidRequest",
    "ERR_UNAUTHORIZED",
)

_AUTH_HINT_SHOWN = False


def describe_http_error(response) -> str:
    """把知乎的错误响应翻译成可操作的中文提示。"""
    text = response.text or ""
    if response.status_code == 401 or any(marker in text for marker in AUTH_ERROR_MARKERS):
        return "知乎返回 401：登录票据已过期，请重新导出 cookies.json"
    if response.status_code == 403:
        return "知乎返回 403：访问被拒绝或触发风控，可尝试加大 requestDelay"
    if response.status_code == 404:
        return "知乎返回 404：收藏夹不存在或已被删除"
    return "HTTP %s" % response.status_code


def warn_auth_expired(hint: str) -> None:
    """登录态失效时，只提示一次醒目的解决办法。"""
    global _AUTH_HINT_SHOWN
    if _AUTH_HINT_SHOWN or "401" not in hint:
        return
    _AUTH_HINT_SHOWN = True
    print("!" * 60)
    print("cookies 已失效，知乎接口拒绝访问（ERR_LOGIN_TICKET_EXPIRED）")
    print("解决办法：重新导出登录态 cookies.json（参考 README「cookies 怎么准备」）")
    print("!" * 60)


@dataclass
class CollectionItem:
    """收藏夹里的一个条目。"""

    title: str
    url: str
    type: str = "answer"
    item_id: str = ""

    @property
    def supported(self) -> bool:
        """该类型是否能被转换成 Markdown 正文。"""
        return self.type in SUPPORTED_TYPES

    @property
    def type_label(self) -> str:
        """类型的中文名，用于日志与汇总。"""
        return TYPE_LABELS.get(self.type, self.type or "未知")

    def as_log(self) -> Dict[str, Any]:
        return {"name": self.title, "url": self.url, "type": self.type}


class CollectionFetchError(RuntimeError):
    """收藏夹列表抓取失败。"""


def parse_collection_id(url_or_id: str) -> str:
    """从收藏夹链接或裸 ID 中取出收藏夹 ID。"""
    value = str(url_or_id or "").strip().replace("\n", "")
    match = _COLLECTION_URL_RE.search(value)
    if match:
        return match.group(1)
    return value.rstrip("/").split("/")[-1].split("?")[0]


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _item_title(content: Dict[str, Any]) -> str:
    """按内容类型取一个可读标题。"""
    ctype = content.get("type")
    if ctype == "answer":
        question = content.get("question") or {}
        return _clean_text(question.get("title")) or _clean_text(content.get("title")) or "未命名回答"
    if ctype == "pin":
        for key in ("excerpt_title", "title"):
            if _clean_text(content.get(key)):
                return _clean_text(content[key])
        return _clean_text(content.get("excerpt"))[:60] or "未命名想法"
    for key in ("title", "excerpt_title"):
        if _clean_text(content.get(key)):
            return _clean_text(content[key])
    question = content.get("question") or {}
    if _clean_text(question.get("title")):
        return _clean_text(question.get("title"))
    return _clean_text(content.get("excerpt"))[:60] or "未命名内容"


def _parse_item(content: Dict[str, Any]) -> Optional[CollectionItem]:
    """把接口返回的一条内容解析成 :class:`CollectionItem`。"""
    url = _clean_text(content.get("url"))
    if not url:
        return None
    ctype = _clean_text(content.get("type")) or "answer"
    item_id = _clean_text(content.get("id"))
    return CollectionItem(title=_item_title(content), url=url, type=ctype, item_id=item_id)


def get_collection_total(
    session, collection_id: str, limiter: Optional[RateLimiter] = None
) -> int:
    """获取收藏夹的条目总数，失败返回 0。"""
    url = "{}?offset=0&limit=1".format(COLLECTION_ITEMS_API.format(collection_id=collection_id))
    try:
        if limiter:
            limiter.wait()
        response = session.get(url, timeout=20)
        if response.status_code != 200:
            hint = describe_http_error(response)
            logging.error("获取收藏夹 %s 总数失败: %s", collection_id, hint)
            warn_auth_expired(hint)
            return 0
        total = int((response.json().get("paging") or {}).get("totals") or 0)
        logging.info("收藏夹 %s 包含 %s 个项目", collection_id, total)
        return total
    except Exception as exc:  # noqa: BLE001
        logging.error("获取收藏夹 %s 总数失败: %s", collection_id, exc)
        return 0


def fetch_collection_items(
    session,
    collection_id: str,
    page_size: int = DEFAULT_PAGE_SIZE,
    limiter: Optional[RateLimiter] = None,
    max_items: Optional[int] = None,
) -> List[CollectionItem]:
    """抓取收藏夹内的全部条目。

    :param max_items: 上限，便于调试时只抓前 N 条
    :return: 条目列表（接口中断时返回已抓到的部分，不抛异常）
    """
    page_size = max(1, min(int(page_size or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
    total = get_collection_total(session, collection_id, limiter)
    if not total:
        logging.warning("收藏夹 %s 没有条目或获取失败", collection_id)
        return []

    items: List[CollectionItem] = []
    offset = 0
    unsupported: List[str] = []

    while offset < total:
        url = "{}?offset={}&limit={}".format(
            COLLECTION_ITEMS_API.format(collection_id=collection_id), offset, page_size
        )
        try:
            logging.debug("请求收藏夹 API: offset=%s, limit=%s", offset, page_size)
            if limiter:
                limiter.wait()
            response = session.get(url, timeout=20)
            if response.status_code != 200:
                hint = describe_http_error(response)
                logging.error("请求收藏夹 API 失败（offset=%s）: %s", offset, hint)
                print("收藏夹 API 请求中断，已获取 %d 条（%s）" % (len(items), hint))
                warn_auth_expired(hint)
                break
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - 中断时保留已获取结果
            logging.error("请求收藏夹 API 失败（offset=%s）: %s", offset, exc)
            print("收藏夹 API 请求中断，已获取 %d 条" % len(items))
            break

        batch = payload.get("data") or []
        if not batch:
            break

        for raw in batch:
            content = raw.get("content") or {}
            item = _parse_item(content)
            if item is None:
                logging.warning("跳过无法解析的条目: %s", str(content)[:120])
                continue
            if not item.supported:
                unsupported.append("{}（{}）".format(item.title, item.type_label))
                logging.info("跳过暂不支持的内容类型 %s: %s", item.type_label, item.url)
                continue
            items.append(item)
            if max_items and len(items) >= max_items:
                break

        if max_items and len(items) >= max_items:
            break

        if (payload.get("paging") or {}).get("is_end"):
            break

        offset += page_size

    logging.info("收藏夹 %s 共解析出 %d 个可导出条目", collection_id, len(items))
    if unsupported:
        preview = "、".join(unsupported[:3])
        more = "等 %d 条" % len(unsupported) if len(unsupported) > 3 else ""
        print("提示：有 %d 条内容暂不支持导出（%s%s）" % (len(unsupported), preview, more))

    return items


def _parse_mine_page_html(html: str) -> List[Dict[str, str]]:
    """从「我的收藏夹」页面解析收藏夹列表。

    主策略：``SelfCollectionItem`` 结构；
    兜底策略：扫描所有 ``/collection/<id>`` 链接（页面改版时仍有机会命中）。
    """
    soup = BeautifulSoup(html, "lxml")
    collections: List[Dict[str, str]] = []
    seen = set()

    for item in soup.find_all(class_="SelfCollectionItem"):
        title_element = item.find(class_="SelfCollectionItem-title")
        if not title_element:
            continue
        link = title_element.find("a")
        href = link.get("href") if link else None
        if not href:
            continue
        collections.append({"name": _clean_text(title_element.get_text()), "url": _absolute(href)})
        seen.add(_collection_key(href))

    if collections:
        return collections

    # 兜底：页面结构变了也能捞到一部分
    for link in soup.find_all("a", href=True):
        href = link.get("href") or ""
        if not _COLLECTION_URL_RE.search(href):
            continue
        key = _collection_key(href)
        if key in seen:
            continue
        name = _clean_text(link.get_text())
        if not name or len(name) > 60:
            continue
        seen.add(key)
        collections.append({"name": name, "url": _absolute(href)})

    if collections:
        logging.info("使用兜底策略从页面解析出 %d 个收藏夹", len(collections))
    return collections


def _absolute(href: str) -> str:
    return href if href.startswith("http") else "https://www.zhihu.com" + href


def _collection_key(href: str) -> str:
    match = _COLLECTION_URL_RE.search(href)
    return match.group(1) if match else href


def _fetch_mine_via_api(session, limiter: Optional[RateLimiter]) -> List[Dict[str, str]]:
    """通过接口获取「我的收藏夹」，需要登录态 cookies。"""
    if limiter:
        limiter.wait()
    response = session.get(ME_API, timeout=20)
    response.raise_for_status()
    token = (response.json() or {}).get("url_token")
    if not token:
        raise CollectionFetchError("接口未返回 url_token，可能未登录")

    collections: List[Dict[str, str]] = []
    offset = 0
    limit = 20
    while True:
        params = {"offset": offset, "limit": limit}
        if limiter:
            limiter.wait()
        response = session.get(MEMBER_COLLECTIONS_API.format(token=token), params=params, timeout=20)
        if response.status_code != 200:
            raise CollectionFetchError("收藏夹接口返回 HTTP %s" % response.status_code)
        payload = response.json() or {}
        batch = payload.get("data") or []
        if not batch:
            break
        for raw in batch:
            cid = raw.get("id")
            url = _clean_text(raw.get("url")) or "https://www.zhihu.com/collection/%s" % cid
            collections.append({"name": _clean_text(raw.get("title")) or "未命名收藏夹", "url": url})
        if (payload.get("paging") or {}).get("is_end") or len(batch) < limit:
            break
        offset += limit

    return collections


def fetch_mine_collections(
    session,
    limiter: Optional[RateLimiter] = None,
    max_pages: int = 50,
    page_delay: Sequence[float] = (1.0, 3.0),
) -> List[Dict[str, str]]:
    """获取当前账号的全部收藏夹。

    先尝试 JSON 接口（稳定、分页明确），失败则回退到 HTML 翻页解析。
    """
    try:
        collections = _fetch_mine_via_api(session, limiter)
        if collections:
            logging.info("通过接口获取到 %d 个收藏夹", len(collections))
            return collections
        logging.warning("接口未返回收藏夹，回退到 HTML 解析")
    except Exception as exc:  # noqa: BLE001 - 回退到 HTML 是预期路径
        logging.warning("通过接口获取收藏夹失败，回退到 HTML 解析: %s", exc)

    return _fetch_mine_via_html(session, limiter, max_pages, page_delay)


def _fetch_mine_via_html(
    session,
    limiter: Optional[RateLimiter] = None,
    max_pages: int = 50,
    page_delay: Sequence[float] = (1.0, 3.0),
) -> List[Dict[str, str]]:
    """HTML 翻页解析「我的收藏夹」。"""
    all_collections: List[Dict[str, str]] = []
    seen = set()

    for page in range(1, max_pages + 1):
        logging.info("正在获取第%s页收藏夹...", page)
        print("正在获取第%s页收藏夹..." % page)

        url = MINE_PAGE_URL.format(page=page)
        try:
            if limiter:
                limiter.wait()
            response = session.get(url, timeout=20)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logging.error("获取第%s页收藏夹失败: %s", page, exc)
            break

        page_collections = _parse_mine_page_html(response.text)
        if not page_collections:
            logging.info("第%s页没有更多收藏夹，结束获取", page)
            print("第%s页没有更多收藏夹，结束获取" % page)
            break

        new_count = 0
        for collection in page_collections:
            key = _collection_key(collection["url"])
            if key in seen:
                continue
            seen.add(key)
            all_collections.append(collection)
            new_count += 1

        print("第%s页获取到%s个收藏夹" % (page, new_count))
        logging.info("第%s页获取到%s个收藏夹", page, new_count)

        if not new_count:  # 页面在重复返回同一批数据，说明已翻到底
            break

        time.sleep(random.uniform(*page_delay))

    return all_collections
