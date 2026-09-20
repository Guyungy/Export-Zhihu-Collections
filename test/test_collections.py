# -*- coding: utf-8 -*-
"""收藏夹抓取与条目解析测试（全部离线）。"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from helpers import FakeResponse, FakeSession, collection_items_payload

from zhihu_export.collections import (
    CollectionItem,
    _fetch_mine_via_html,
    _item_title,
    _parse_item,
    _parse_mine_page_html,
    describe_http_error,
    fetch_collection_items,
    get_collection_total,
    parse_collection_id,
)


class TestAuthHints:
    """登录票据过期时要给出可操作的提示，而不是一句「没获取到」。"""

    def test_describe_401(self):
        response = FakeResponse(
            text='{"error":{"name":"AuthenticationInvalidRequest","message":"ERR_LOGIN_TICKET_EXPIRED"}}',
            status_code=401,
        )
        hint = describe_http_error(response)
        assert "401" in hint and "cookies" in hint

    def test_describe_403_suggests_delay(self):
        assert "requestDelay" in describe_http_error(FakeResponse(status_code=403))

    def test_describe_404(self):
        assert "404" in describe_http_error(FakeResponse(status_code=404))

    def test_get_total_on_401_returns_zero(self):
        session = FakeSession(lambda url: FakeResponse(text="ERR_LOGIN_TICKET_EXPIRED", status_code=401))
        assert get_collection_total(session, "343826248") == 0

    def test_items_on_401_returns_empty(self):
        session = FakeSession(lambda url: FakeResponse(text="ERR_LOGIN_TICKET_EXPIRED", status_code=401))
        assert fetch_collection_items(session, "343826248") == []


class TestParseCollectionId:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("https://www.zhihu.com/collection/343826248", "343826248"),
            ("https://www.zhihu.com/collection/343826248?page=2", "343826248"),
            ("https://www.zhihu.com/collection/343826248/", "343826248"),
            ("343826248\n", "343826248"),
        ],
    )
    def test_variants(self, value, expected):
        assert parse_collection_id(value) == expected


class TestItemParsing:
    def test_answer_title_from_question(self):
        content = {"type": "answer", "url": "https://www.zhihu.com/question/1/answer/2",
                   "question": {"title": "  问题   标题 "}}
        item = _parse_item(content)
        assert item is not None
        assert item.title == "问题 标题"
        assert item.type == "answer"
        assert item.supported

    def test_article_title(self):
        item = _parse_item({"type": "article", "url": "https://zhuanlan.zhihu.com/p/1", "title": "专栏标题"})
        assert item is not None
        assert item.title == "专栏标题"
        assert item.type_label == "专栏"

    def test_pin_uses_excerpt_when_no_title(self):
        content = {"type": "pin", "url": "https://www.zhihu.com/pin/1", "excerpt": "这是一条想法正文内容"}
        assert _item_title(content) == "这是一条想法正文内容"

    def test_unsupported_type_flagged(self):
        item = _parse_item({"type": "zvideo", "url": "https://www.zhihu.com/zvideo/1", "title": "视频"})
        assert item is not None
        assert not item.supported
        assert item.type_label == "视频"

    def test_missing_url_returns_none(self):
        assert _parse_item({"type": "answer"}) is None

    def test_collection_item_log_shape(self):
        item = CollectionItem(title="t", url="u", type="answer")
        assert item.as_log() == {"name": "t", "url": "u", "type": "answer"}


class TestCollectionItems:
    def _session_for_pages(self, pages: List[Dict[str, Any]]) -> FakeSession:
        def handler(url: str) -> FakeResponse:
            # 收藏夹条目接口：用 offset 选页
            for offset, payload in pages:
                if "offset=%d" % offset in url:
                    return FakeResponse(json_data=payload)
            raise AssertionError("未预期的请求: %s" % url)

        return FakeSession(handler)

    def test_pagination_and_unsupported_skipped(self):
        page1 = collection_items_payload(
            [
                {"type": "answer", "url": "https://www.zhihu.com/question/1/answer/1", "question": {"title": "回答A"}},
                {"type": "zvideo", "url": "https://www.zhihu.com/zvideo/9", "title": "视频B"},
            ],
            totals=4,
            is_end=False,
        )
        page2 = collection_items_payload(
            [
                {"type": "article", "url": "https://zhuanlan.zhihu.com/p/2", "title": "专栏C"},
            ],
            totals=4,
            is_end=False,
        )
        session = self._session_for_pages([(0, page1), (2, page2)])
        # 第一页 limit 默认 20，第二页 offset 会前进 20，这里改成小页宽来验证分页逻辑
        items = fetch_collection_items(session, "1", page_size=2)
        titles = [item.title for item in items]
        assert titles == ["回答A", "专栏C"]
        assert all(item.supported for item in items)

    def test_api_error_returns_partial_list(self):
        def handler(url: str) -> FakeResponse:
            if "limit=1" in url:
                return FakeResponse(json_data={"paging": {"totals": 5}})
            return FakeResponse(status_code=500)

        items = fetch_collection_items(FakeSession(handler), "1", page_size=2)
        assert items == []

    def test_zero_total_returns_empty(self):
        session = FakeSession(lambda url: FakeResponse(json_data={"paging": {"totals": 0}}))
        assert fetch_collection_items(session, "1") == []

    def test_total_error_returns_zero(self):
        def handler(url: str) -> FakeResponse:
            raise RuntimeError("boom")

        assert get_collection_total(FakeSession(handler), "1") == 0

    def test_max_items_limits_result(self):
        payload_page1 = collection_items_payload(
            [{"type": "answer", "url": "https://www.zhihu.com/question/1/answer/%d" % i,
              "question": {"title": "回答%d" % i}} for i in range(3)],
            totals=10,
            is_end=False,
        )
        session = self._session_for_pages([(0, payload_page1)])
        items = fetch_collection_items(session, "1", page_size=3, max_items=2)
        assert len(items) == 2


class TestMinePageParsing:
    def test_primary_selector(self, mine_page_html):
        collections = _parse_mine_page_html(mine_page_html)
        assert [c["name"] for c in collections] == ["技术-效率工具", "赚钱-金融市场"]
        assert collections[0]["url"] == "https://www.zhihu.com/collection/343826248"
        assert collections[1]["url"].startswith("https://www.zhihu.com/collection/")

    def test_fallback_link_scan_dedupes(self, mine_page_v2_html):
        collections = _parse_mine_page_html(mine_page_v2_html)
        assert len(collections) == 2
        assert collections[0]["name"] == "技术-效率工具"

    def test_html_paging_stops_on_repeat(self, mine_page_html):
        session = FakeSession(lambda url: FakeResponse(text=mine_page_html))
        collections = _fetch_mine_via_html(session, max_pages=5, page_delay=(0, 0))
        assert len(collections) == 2
        assert len(session.calls) == 2  # 第二页返回同一批 → 判定到底并停止

    def test_html_paging_stops_on_empty_page(self, mine_page_v2_html):
        def handler(url: str) -> FakeResponse:
            if "page=1" in url:
                return FakeResponse(text=mine_page_v2_html)
            return FakeResponse(text="<html><body>没有收藏夹</body></html>")

        session = FakeSession(handler)
        collections = _fetch_mine_via_html(session, max_pages=5, page_delay=(0, 0))
        assert len(collections) == 2
        assert len(session.calls) == 2
