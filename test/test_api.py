# -*- coding: utf-8 -*-
"""正文 API 兜底与图片懒加载提升的离线测试。

覆盖两条路线：

1. ``zhihu_export.api`` —— URL 解析、分块拼装、异常收敛
2. ``main`` —— 页面被 403 / 解析不出容器时是否正确回退到 API，
   以及专栏文章是否走流式读取
"""

from __future__ import annotations

import logging

import pytest
from helpers import FakeResponse, FakeSession

import main
from zhihu_export import api as api_mod
from zhihu_export import http as http_mod
from zhihu_export.converter import promote_lazy_images, sanitize_content
from zhihu_export.http import RateLimiter

ANSWER_URL = "https://www.zhihu.com/question/1/answer/2"
POST_URL = "https://zhuanlan.zhihu.com/p/386395767"
PIN_URL = "https://www.zhihu.com/pin/123456"

ANSWER_CONTENT = "<p>这是 API 返回的回答正文</p>"
ARTICLE_CONTENT = "<p>这是 API 返回的专栏正文</p>"


@pytest.fixture()
def isolated_env(tmp_path, monkeypatch):
    """隔离输出目录、日志与全局状态。"""
    previous_handlers = logging.getLogger().handlers[:]

    monkeypatch.setattr(main, "base_output_path", tmp_path)
    monkeypatch.setattr(
        main,
        "config",
        {
            "outputPath": str(tmp_path),
            "downloadWorkers": 1,
            "imageWorkers": 1,
            "requestDelay": 0,
            "fetchRetries": 1,
        },
    )
    monkeypatch.setattr(main, "request_limiter", RateLimiter(0))
    monkeypatch.setattr(main, "fetch_stats", {"api_fallback": 0, "page_retries": 0})
    main._mark_content_source("page")
    main.processing_log = []

    main.reconfigure_logging()
    try:
        yield tmp_path
    finally:
        logging.getLogger().handlers[:] = previous_handlers


# ---------------------------------------------------------------------------
# URL 解析
# ---------------------------------------------------------------------------


class TestParseContentTarget:
    @pytest.mark.parametrize(
        "url,expected",
        [
            (ANSWER_URL, ("answer", "2")),
            (ANSWER_URL + "?utm_source=wechat", ("answer", "2")),
            ("https://www.zhihu.com/question/1/answer/2/", ("answer", "2")),
            ("https://zhuanlan.zhihu.com/p/386395767", ("article", "386395767")),
            ("https://zhuanlan.zhihu.com/p/123?utm_psn=1", ("article", "123")),
            ("https://www.zhihu.com/pin/123456", ("pin", "123456")),
        ],
    )
    def test_recognizes_supported_urls(self, url, expected):
        assert api_mod.parse_content_target(url) == expected

    @pytest.mark.parametrize(
        "url",
        ["", "https://www.zhihu.com/question/1", "https://www.zhihu.com/collection/9", "not a url"],
    )
    def test_returns_none_for_unknown_urls(self, url):
        assert api_mod.parse_content_target(url) is None

    def test_answer_rule_wins_over_generic_id_rule(self):
        """``/answer/`` 必须优先匹配，否则会被其它数字规则抢走。"""
        kind, content_id = api_mod.parse_content_target(ANSWER_URL)
        assert (kind, content_id) == ("answer", "2")


class TestBuildApiUrl:
    def test_builds_expected_urls(self):
        assert api_mod.build_api_url("answer", "2").endswith("/api/v4/answers/2?include=content")
        assert api_mod.build_api_url("article", "5").endswith("/api/v4/articles/5?include=content")
        assert api_mod.build_api_url("pin", "7").endswith("/api/v4/pins/7")

    def test_unknown_kind_returns_none(self):
        assert api_mod.build_api_url("video", "1") is None


# ---------------------------------------------------------------------------
# 返回体解析
# ---------------------------------------------------------------------------


class TestExtractContentHtml:
    def test_answer_payload(self):
        assert api_mod.extract_content_html({"content": ANSWER_CONTENT}, "answer") == ANSWER_CONTENT

    def test_article_payload(self):
        assert api_mod.extract_content_html({"content": ARTICLE_CONTENT}, "article") == ARTICLE_CONTENT

    def test_error_payload_returns_none(self):
        payload = {"error": {"code": 1000, "message": "unauthorized"}}
        assert api_mod.extract_content_html(payload, "answer") is None

    def test_blank_content_returns_none(self):
        assert api_mod.extract_content_html({"content": "   "}, "answer") is None
        assert api_mod.extract_content_html({}, "answer") is None

    def test_non_dict_payload_returns_none(self):
        assert api_mod.extract_content_html([], "answer") is None
        assert api_mod.extract_content_html(None, "answer") is None

    def test_pin_blocks_are_rendered(self):
        payload = {
            "content": [
                {"type": "text", "content": "<p>想法正文</p>"},
                {"type": "image", "url": "https://pic1.zhimg.com/a.jpg"},
                {"type": "link", "url": "https://example.com", "title": "卡片标题"},
                {"type": "video", "title": "一段视频"},
                {"type": "title", "content": "小标题"},
                {"type": "mystery", "content": "<p>未知块</p>"},
                {"type": "mystery"},
            ]
        }
        html = api_mod.extract_content_html(payload, "pin")
        assert "想法正文" in html
        assert 'src="https://pic1.zhimg.com/a.jpg"' in html
        assert '<a href="https://example.com">卡片标题</a>' in html
        assert "一段视频" in html
        assert "<h2>小标题</h2>" in html
        assert "未知块" in html  # 未知类型不静默丢内容

    def test_pin_without_content_returns_none(self):
        assert api_mod.extract_content_html({"content": []}, "pin") is None


# ---------------------------------------------------------------------------
# 请求层
# ---------------------------------------------------------------------------


def _api_session(handler):
    return FakeSession(handler)


class TestDescribeApiError:
    """用真实抓到的错误体做回归，避免又退回「一句 HTTP 403」。"""

    def test_need_login(self):
        payload = {
            "error": {
                "need_login": True,
                "redirect": "https://www.zhihu.com/account/unhuman?type=U4E3Z1",
            }
        }
        assert "重新导出" in api_mod.describe_api_error(payload)

    def test_login_ticket_expired(self):
        payload = {
            "error": {
                "code": 100,
                "name": "AuthenticationInvalidRequest",
                "message": "ERR_LOGIN_TICKET_EXPIRED",
            }
        }
        hint = api_mod.describe_api_error(payload)
        assert "登录票据" in hint
        assert "ERR_LOGIN_TICKET_EXPIRED" in hint

    def test_risk_control_code_10003(self):
        payload = {
            "error": {"message": "请求参数异常，请升级客户端后重试。", "code": 10003}
        }
        hint = api_mod.describe_api_error(payload)
        assert "风控" in hint
        assert "10003" in hint

    def test_generic_error_keeps_message(self):
        payload = {"error": {"message": "炸了", "code": 500}}
        assert api_mod.describe_api_error(payload) == "炸了 (code=500)"

    @pytest.mark.parametrize("payload", [{}, {"content": "x"}, {"error": "字符串"}, None, []])
    def test_no_error_returns_empty(self, payload):
        assert api_mod.describe_api_error(payload) == ""


class TestFetchContentViaApi:
    def test_success_returns_html(self):
        def handler(url):
            return FakeResponse(
                json_data={"content": ANSWER_CONTENT},
                headers={"Content-Type": "application/json"},
            )

        assert api_mod.fetch_content_via_api(_api_session(handler), ANSWER_URL) == ANSWER_CONTENT

    def test_sends_api_headers(self):
        session = _api_session(
            lambda url: FakeResponse(
                json_data={"content": ANSWER_CONTENT},
                headers={"Content-Type": "application/json"},
            )
        )
        api_mod.fetch_content_via_api(session, ANSWER_URL)

        headers = session.calls_detail[0]["kwargs"]["headers"]
        assert headers["x-requested-with"] == "fetch"
        assert headers["Sec-Fetch-Mode"] == "cors"

    def test_unparsable_url_makes_no_request(self):
        session = _api_session(lambda url: FakeResponse(json_data={}))
        assert api_mod.fetch_content_via_api(session, "https://www.zhihu.com/question/1") is None
        assert session.calls == []

    def test_http_error_is_swallowed(self):
        session = _api_session(lambda url: FakeResponse(status_code=403, text="forbidden"))
        assert api_mod.fetch_content_via_api(session, ANSWER_URL) is None

    def test_non_json_body_is_rejected(self):
        """风控页会返回 200 + HTML，必须当失败处理，不能拿去解析。"""
        session = _api_session(
            lambda url: FakeResponse(text="<html>安全验证</html>", headers={"Content-Type": "text/html"})
        )
        assert api_mod.fetch_content_via_api(session, ANSWER_URL) is None

    def test_broken_json_is_swallowed(self):
        session = _api_session(
            lambda url: FakeResponse(text="{not json", headers={"Content-Type": "application/json"})
        )
        assert api_mod.fetch_content_via_api(session, ANSWER_URL) is None

    def test_limiter_is_used(self):
        waits = []

        class CountingLimiter:
            def wait(self):
                waits.append(1)

        api_mod.fetch_content_via_api(
            _api_session(
                lambda url: FakeResponse(
                    json_data={"content": ANSWER_CONTENT},
                    headers={"Content-Type": "application/json"},
                )
            ),
            ANSWER_URL,
            CountingLimiter(),
        )
        assert waits == [1]


# ---------------------------------------------------------------------------
# main.py 集成
# ---------------------------------------------------------------------------


def _page_then_api(page_response, payload, content_type="application/json"):
    """页面返回 page_response，API 返回 payload。"""

    def handler(url):
        if "/api/v4/" in url:
            return FakeResponse(json_data=payload, headers={"Content-Type": content_type})
        return page_response

    return FakeSession(handler)


class TestAnswerApiFallback:
    def test_403_on_page_falls_back_to_api(self, isolated_env, monkeypatch):
        session = _page_then_api(
            FakeResponse(status_code=403, text="forbidden", url=ANSWER_URL),
            {"content": ANSWER_CONTENT},
        )
        monkeypatch.setattr(main, "session", session)

        html = main.get_single_answer_content(ANSWER_URL)

        assert html != main.FETCH_FAILED
        assert "这是 API 返回的回答正文" in html
        assert any("/api/v4/answers/2" in url for url in session.calls)
        assert main.fetch_stats["api_fallback"] == 1

    def test_page_without_container_falls_back_to_api(self, isolated_env, monkeypatch):
        """页面返回 200 但结构改版、找不到容器时，同样要走 API 兜底。"""
        session = _page_then_api(
            FakeResponse(text="<html><body><div>占位</div></body></html>", url=ANSWER_URL),
            {"content": ANSWER_CONTENT},
        )
        monkeypatch.setattr(main, "session", session)

        html = main.get_single_answer_content(ANSWER_URL)

        assert "这是 API 返回的回答正文" in html
        assert main.fetch_stats["api_fallback"] == 1

    def test_fallback_can_be_disabled(self, isolated_env, monkeypatch):
        session = _page_then_api(
            FakeResponse(status_code=403, text="forbidden", url=ANSWER_URL),
            {"content": ANSWER_CONTENT},
        )
        monkeypatch.setattr(main, "session", session)
        monkeypatch.setattr(main, "config", dict(main.config, apiFallback=False))

        assert main.get_single_answer_content(ANSWER_URL) == main.FETCH_FAILED
        assert not any("/api/v4/" in url for url in session.calls)

    def test_both_routes_failing_returns_failed(self, isolated_env, monkeypatch):
        session = _page_then_api(
            FakeResponse(status_code=403, text="forbidden", url=ANSWER_URL),
            {"error": {"code": 1000, "message": "unauthorized"}},
        )
        monkeypatch.setattr(main, "session", session)

        assert main.get_single_answer_content(ANSWER_URL) == main.FETCH_FAILED
        assert main.fetch_stats["api_fallback"] == 0

    def test_pin_url_uses_pin_api(self, isolated_env, monkeypatch):
        payload = {"content": [{"type": "text", "content": "<p>想法正文</p>"}]}
        session = _page_then_api(
            FakeResponse(status_code=403, text="forbidden", url=PIN_URL),
            payload,
        )
        monkeypatch.setattr(main, "session", session)

        html = main.get_single_answer_content(PIN_URL)

        assert "想法正文" in html
        assert any("/api/v4/pins/123456" in url for url in session.calls)


class TestPostApiFallback:
    def test_streamed_page_is_parsed(self, isolated_env, monkeypatch):
        """专栏走流式读取，装饰性 HTML 也要能正常解析。"""
        html = '<html><body><div class="Post-RichText"><p>专栏正文</p></div></body></html>'
        session = _page_then_api(FakeResponse(text=html, url=POST_URL), {"content": ARTICLE_CONTENT})
        monkeypatch.setattr(main, "session", session)

        content = main.get_single_post_content(POST_URL)

        assert "专栏正文" in content
        assert session.calls_detail[0]["kwargs"].get("stream") is True

    def test_403_falls_back_to_article_api(self, isolated_env, monkeypatch):
        session = _page_then_api(
            FakeResponse(status_code=403, text="forbidden", url=POST_URL),
            {"content": ARTICLE_CONTENT},
        )
        monkeypatch.setattr(main, "session", session)

        html = main.get_single_post_content(POST_URL)

        assert "这是 API 返回的专栏正文" in html
        assert any("/api/v4/articles/386395767" in url for url in session.calls)

    def test_retry_then_success(self, isolated_env, monkeypatch):
        """首次 500（urllib3 之外的应用层重试）应能自行恢复。"""
        attempts = {"n": 0}

        def handler(url):
            if url == POST_URL:
                attempts["n"] += 1
                if attempts["n"] == 1:
                    return FakeResponse(status_code=500, text="boom", url=POST_URL)
                return FakeResponse(
                    text='<html><body><div class="Post-RichText"><p>重试成功</p></div></body></html>',
                    url=POST_URL,
                )
            return None

        session = FakeSession(handler)
        monkeypatch.setattr(main, "session", session)
        monkeypatch.setattr(main, "config", dict(main.config, fetchRetries=2))
        monkeypatch.setattr(main.time, "sleep", lambda *_a: None)  # 别真的等 2 秒

        content = main.get_single_post_content(POST_URL)

        assert "重试成功" in content
        assert attempts["n"] == 2
        assert main.fetch_stats["page_retries"] == 1

    def test_never_retries_403(self, isolated_env, monkeypatch):
        """403 重试没意义，应立刻转 API 兜底而不是白等。"""
        attempts = {"n": 0}

        def handler(url):
            if url == POST_URL:
                attempts["n"] += 1
                return FakeResponse(status_code=403, text="forbidden", url=POST_URL)
            return FakeResponse(
                json_data={"content": ARTICLE_CONTENT}, headers={"Content-Type": "application/json"}
            )

        monkeypatch.setattr(main, "session", FakeSession(handler))
        monkeypatch.setattr(main, "config", dict(main.config, fetchRetries=3))
        monkeypatch.setattr(main.time, "sleep", lambda *_a: None)

        main.get_single_post_content(POST_URL)
        assert attempts["n"] == 1


class TestApiFallbackEndToEnd:
    """整条链路：页面 403 → API 兜底 → 图片落地 → 写出 Markdown。"""

    def test_answer_via_api_with_lazy_image_writes_markdown(self, isolated_env, monkeypatch):
        image_url = "https://pic1.zhimg.com/v2-real.jpg"
        api_content = (
            '<p>正文第一段</p>'
            '<img src="data:image/svg+xml;base64,PLACEHOLDER" data-original="%s" alt="配图">'
            % image_url
        )

        def handler(url):
            if url == ANSWER_URL:
                return FakeResponse(status_code=403, text="forbidden", url=ANSWER_URL)
            if "/api/v4/" in url:
                return FakeResponse(
                    json_data={"content": api_content},
                    headers={"Content-Type": "application/json"},
                )
            if url == image_url:
                return FakeResponse(
                    content=b"\xff\xd8\xff real-image-bytes",
                    headers={"Content-Type": "image/jpeg"},
                )
            return None

        session = FakeSession(handler)
        monkeypatch.setattr(main, "session", session)

        from zhihu_export.converter import ImageDownloader

        assets = isolated_env / "assets"
        md_path = isolated_env / "answer.md"
        task = {
            "title": "通过 API 兜底导出的回答",
            "url": ANSWER_URL,
            "type": "answer",
            "file_path": str(md_path),
            "image_downloader": ImageDownloader(session, assets, max_workers=2),
        }

        log = main.download_single_article(task)

        assert log["status"] == "正常下载"
        assert log["source"] == "api"

        md = md_path.read_text(encoding="utf-8")
        assert md.startswith("> %s" % ANSWER_URL)
        assert "正文第一段" in md
        # 图片被本地化，且没有留下远程链接或占位图
        assert "![[v2-real.jpg]]" in md
        assert image_url not in md
        assert "data:image/svg+xml" not in md
        assert (assets / "v2-real.jpg").read_bytes() == b"\xff\xd8\xff real-image-bytes"

    def test_lazy_image_would_be_lost_without_promotion(self, isolated_env):
        """反向验证：跳过提升步骤，配图确实会被 sanitize 删掉。"""
        from bs4 import BeautifulSoup

        html = '<p>正文</p><img src="data:image/svg+xml;base64,X" data-original="https://pic1.zhimg.com/a.jpg">'
        soup = BeautifulSoup(html, "lxml")

        for el in soup.select('img[src*="data:image/svg+xml"]'):
            el.extract()

        assert soup.find("img") is None  # 这就是不提升的后果


class TestContentSourceTracking:
    def test_api_source_is_recorded_then_reset(self, isolated_env, monkeypatch):
        session = _page_then_api(
            FakeResponse(status_code=403, text="forbidden", url=ANSWER_URL),
            {"content": ANSWER_CONTENT},
        )
        monkeypatch.setattr(main, "session", session)

        assert main._consume_content_source() == "page"
        main.get_single_answer_content(ANSWER_URL)
        assert main._consume_content_source() == "api"
        # 取过一次就复位，避免污染同线程的下一篇
        assert main._consume_content_source() == "page"


# ---------------------------------------------------------------------------
# 懒加载图片
# ---------------------------------------------------------------------------


class TestPromoteLazyImages:
    def test_data_original_is_promoted(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(
            '<div><img src="data:image/svg+xml;base64,AAA" data-original="https://pic1.zhimg.com/real.jpg"></div>',
            "lxml",
        )
        assert promote_lazy_images(soup) == 1
        assert soup.img["src"] == "https://pic1.zhimg.com/real.jpg"

    def test_data_actualsrc_fallback(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup('<img src="" data-actualsrc="https://picx.zhimg.com/b.jpg">', "lxml")
        assert promote_lazy_images(soup) == 1
        assert soup.img["src"] == "https://picx.zhimg.com/b.jpg"

    def test_real_src_is_untouched(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup('<img src="https://pic1.zhimg.com/already.jpg">', "lxml")
        assert promote_lazy_images(soup) == 0
        assert soup.img["src"] == "https://pic1.zhimg.com/already.jpg"

    def test_sanitize_keeps_promoted_image(self):
        """回归：不做提升时，API 正文里的配图会被当成占位图删掉。"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(
            '<div><img src="data:image/svg+xml;base64,AAA" data-original="https://pic1.zhimg.com/real.jpg">'
            '<style>x{}</style></div>',
            "lxml",
        )
        sanitize_content(soup)

        assert soup.find("img") is not None
        assert soup.find("img")["src"] == "https://pic1.zhimg.com/real.jpg"
        assert soup.find("style") is None

    def test_sanitize_still_drops_unresolvable_placeholder(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup('<img src="data:image/svg+xml;base64,AAA">', "lxml")
        sanitize_content(soup)
        assert soup.find("img") is None


# ---------------------------------------------------------------------------
# 请求头
# ---------------------------------------------------------------------------


class TestHeaders:
    def test_modern_browser_headers(self):
        headers = http_mod.build_headers()
        assert "Chrome/61" not in headers["User-Agent"]
        assert headers["Referer"] == "https://www.zhihu.com/"
        assert headers["sec-ch-ua-platform"] == '"Windows"'
        assert "Sec-Fetch-Mode" in headers

    def test_accept_encoding_only_claims_available_decoders(self):
        """声明了却解不开的编码会让正文变成乱码，必须按实际能力协商。"""
        declared = http_mod.DEFAULT_HEADERS["Accept-Encoding"]
        assert "gzip" in declared
        assert "deflate" in declared

        def _importable(*names: str) -> bool:
            for name in names:
                try:
                    __import__(name)
                    return True
                except ImportError:
                    continue
            return False

        assert ("br" in declared) == _importable("brotli", "brotlicffi")
        assert ("zstd" in declared) == _importable("zstandard")

    def test_api_headers_are_separate(self):
        api_headers = http_mod.build_api_headers()
        assert api_headers["x-requested-with"] == "fetch"
        assert api_headers["Sec-Fetch-Dest"] == "empty"
