# -*- coding: utf-8 -*-
"""HTML → Markdown 转换与图片下载测试（全部离线）。"""

from __future__ import annotations

import pathlib
from typing import Optional

import pytest
import requests
from bs4 import BeautifulSoup
from helpers import FakeResponse, FakeSession

from zhihu_export.converter import (
    ImageDownloader,
    ObsidianStyleConverter,
    html_template,
    markdownify,
    prefetch_content_images,
    safe_filename,
    sanitize_content,
)

PIC_URL = "https://pic1.zhimg.com/v2-demo-pic_1440w.jpg"


def _image_handler(url: str) -> Optional[FakeResponse]:
    if "zhimg.com" in url:
        return FakeResponse(content=b"\xff\xd8\xff fake-jpeg", headers={"Content-Type": "image/jpeg"})
    return None


def convert_answer(html: str, session, assets_dir) -> tuple:
    """走一遍真实链路：解析 → 清理 → 预取图片 → 转 Markdown。"""
    soup = BeautifulSoup(html, "lxml")
    node = soup.select_one(".RichContent-inner")
    sanitize_content(node)
    downloader = ImageDownloader(session, assets_dir, max_workers=2)
    prefetch_content_images(node, downloader)
    md = markdownify(html_template(node), image_downloader=downloader)
    return md, downloader


class TestImageDownloader:
    def test_downloads_and_caches(self, tmp_path):
        session = FakeSession(_image_handler)
        downloader = ImageDownloader(session, tmp_path / "assets", max_workers=2)

        assert downloader.fetch(PIC_URL) == "v2-demo-pic_1440w.jpg"
        assert downloader.fetch(PIC_URL) == "v2-demo-pic_1440w.jpg"
        assert len(session.calls) == 1  # 第二次命中缓存
        assert (tmp_path / "assets" / "v2-demo-pic_1440w.jpg").exists()

    def test_extension_guessed_from_content_type(self, tmp_path):
        session = FakeSession(lambda url: FakeResponse(content=b"x", headers={"Content-Type": "image/png"}))
        downloader = ImageDownloader(session, tmp_path, max_workers=1)
        assert downloader.fetch("https://pic1.zhimg.com/v2-noext") == "v2-noext.png"

    def test_same_basename_different_url_gets_suffix(self, tmp_path):
        session = FakeSession(_image_handler)
        downloader = ImageDownloader(session, tmp_path, max_workers=1)
        first = downloader.fetch("https://a.zhimg.com/v2-same.jpg?x=1")
        second = downloader.fetch("https://b.zhimg.com/v2-same.jpg?x=2")
        assert first == "v2-same.jpg"
        assert second != first and second.startswith("v2-same_")

    def test_failure_is_recorded_without_raising(self, tmp_path):
        def handler(url: str) -> FakeResponse:
            raise requests.ConnectionError("网络不可用")

        downloader = ImageDownloader(FakeSession(handler), tmp_path, max_workers=1)
        assert downloader.fetch(PIC_URL) is None
        assert downloader.stats["failed"] == 1

    def test_data_url_skipped(self, tmp_path):
        downloader = ImageDownloader(FakeSession(_image_handler), tmp_path, max_workers=1)
        assert downloader.fetch("data:image/svg+xml;base64,AAAA") is None

    def test_prefetch_dedupes(self, tmp_path):
        session = FakeSession(_image_handler)
        downloader = ImageDownloader(session, tmp_path, max_workers=3)
        downloader.prefetch([PIC_URL, PIC_URL, PIC_URL])
        assert len(session.calls) == 1

    def test_safe_filename(self):
        assert safe_filename('a/b:c*d?"e') == "a_b_c_d_e"
        assert safe_filename("") == "unnamed"


class TestConverter:
    def test_image_uses_obsidian_embed(self, answer_page_html, tmp_path):
        md, downloader = convert_answer(answer_page_html, FakeSession(_image_handler), tmp_path / "assets")
        assert "![[v2-demo-pic_1440w.jpg]]" in md
        assert "(示意图)" in md
        assert downloader.stats["downloaded"] == 1

    def test_image_failure_keeps_remote_link(self, answer_page_html, tmp_path):
        def handler(url: str) -> FakeResponse:
            raise requests.ConnectionError("boom")

        md, downloader = convert_answer(answer_page_html, FakeSession(handler), tmp_path / "assets")
        assert "![示意图](%s)" % PIC_URL in md
        assert downloader.stats["failed"] == 1

    def test_svg_placeholder_removed(self, answer_page_html, tmp_path):
        md, _ = convert_answer(answer_page_html, FakeSession(_image_handler), tmp_path / "assets")
        assert "data:image/svg+xml" not in md

    def test_link_card_uses_card_title_as_link_text(self, answer_page_html, tmp_path):
        """链接卡片降级：用卡片标题替换原始 URL 文字。"""
        md, _ = convert_answer(answer_page_html, FakeSession(_image_handler), tmp_path / "assets")
        assert "[卡片标题示例](https://example.com/card)" in md

    def test_mailto_link_downgraded(self, answer_page_html, tmp_path):
        md, _ = convert_answer(answer_page_html, FakeSession(_image_handler), tmp_path / "assets")
        assert "mailto:" not in md
        assert "aaa@bbb.ccc" in md

    def test_heading_style_is_atx(self, answer_page_html, tmp_path):
        md, _ = convert_answer(answer_page_html, FakeSession(_image_handler), tmp_path / "assets")
        assert "## 小标题" in md

    def test_footnote_back_link_has_no_bullet(self, answer_page_html, tmp_path):
        md, _ = convert_answer(answer_page_html, FakeSession(_image_handler), tmp_path / "assets")
        assert "返回引用" in md
        assert "* 返回引用" not in md

    def test_plain_link_kept(self, answer_page_html, tmp_path):
        md, _ = convert_answer(answer_page_html, FakeSession(_image_handler), tmp_path / "assets")
        assert "[普通链接](https://example.com/ref)" in md

    def test_converter_without_downloader_keeps_links(self, answer_page_html):
        soup = BeautifulSoup(answer_page_html, "lxml")
        node = soup.select_one(".RichContent-inner")
        sanitize_content(node)
        converter = ObsidianStyleConverter(image_downloader=None)
        md = converter.convert(html_template(node))
        assert "![示意图](%s)" % PIC_URL in md
        assert converter.stats["image_failures"] == 1

    def test_markdownify_returns_string(self):
        assert "hello" in markdownify("<p>hello</p>")
