# -*- coding: utf-8 -*-
"""pytest 公共夹具。

假对象与样例读取放在 :mod:`helpers`，这里只负责组织 pytest fixture。
所有测试都不需要联网、不需要 cookies。
"""

from __future__ import annotations

from typing import Optional

import pytest
from helpers import FakeResponse, FakeSession, read_fixture


@pytest.fixture()
def answer_page_html() -> str:
    """知乎回答页样例（含图片、链接卡片、mailto、脚注回链）。"""
    return read_fixture("answer_page.html")


@pytest.fixture()
def post_page_html() -> str:
    """知乎专栏页样例。"""
    return read_fixture("post_page.html")


@pytest.fixture()
def mine_page_html() -> str:
    """「我的收藏夹」页面样例（标准结构）。"""
    return read_fixture("mine_collections_page.html")


@pytest.fixture()
def mine_page_v2_html() -> str:
    """「我的收藏夹」页面样例（改版后的结构）。"""
    return read_fixture("mine_collections_page_v2.html")


@pytest.fixture()
def image_session() -> FakeSession:
    """能提供图片字节的假会话。"""

    def handler(url: str) -> Optional[FakeResponse]:
        if "zhimg.com" in url:
            return FakeResponse(content=b"\xff\xd8\xff demo image", headers={"Content-Type": "image/jpeg"})
        return None

    return FakeSession(handler)
