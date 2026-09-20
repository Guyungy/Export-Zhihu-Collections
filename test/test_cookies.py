# -*- coding: utf-8 -*-
"""cookies 载入与「请求头字符集」防线测试（离线）。

对应 issue #1「编码错误」：cookie 里只要有一个中文名/值，requests 会在**建立连接之前**
就抛 ``UnicodeEncodeError: 'latin-1' codec can't encode characters in position 0-1``，
而报错位置落在 ``http.client`` 内部，完全看不出是哪个配置项写坏了。

修复思路是「逐条剔除 + 说清原因」，所以这里既测剔除行为，也测**没被剔除的部分仍可用**。
"""

from __future__ import annotations

import json
import logging

import pytest
import requests

import main
from zhihu_export import http as http_mod

COLLECTION_API = "https://www.zhihu.com/api/v4/collections/1/items"

#: 仓库早期示例文件里的占位条目：名字本身就是中文，用户照抄后必然踩坑。
LEGACY_PLACEHOLDER = {"name": "自用", "value": "Cookie修sasd11asdasd改这里"}


def write_cookies(tmp_path, payload, filename: str = "cookies.json"):
    """把 payload 写成 cookies 文件，返回路径。"""
    path = tmp_path / filename
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def cookie_header_for(cookies):
    """把 cookies 挂到会话上，返回真正会被发出去的 ``Cookie`` 请求头。"""
    session = http_mod.create_session(cookies=cookies)
    prepared = session.prepare_request(requests.Request("GET", COLLECTION_API))
    return prepared.headers.get("Cookie", "")


class TestRootCauseFrozen:
    """把 issue #1 的根因固化成断言，避免以后有人把防线当冗余删掉。"""

    def test_chinese_cookie_name_breaks_latin1_at_position_0(self):
        session = requests.Session()
        session.cookies.update({"自用": "anything"})
        prepared = session.prepare_request(requests.Request("GET", COLLECTION_API))

        with pytest.raises(UnicodeEncodeError) as excinfo:
            prepared.headers["Cookie"].encode("latin-1")

        # 「position 0-1」正对应用户截图里的报错：Cookie 头开头就是这两个汉字
        assert "position 0-1" in str(excinfo.value)

    def test_chinese_cookie_value_breaks_latin1_mid_header(self):
        session = requests.Session()
        session.cookies.update({"z_c0": "中文值"})
        prepared = session.prepare_request(requests.Request("GET", COLLECTION_API))

        with pytest.raises(UnicodeEncodeError):
            prepared.headers["Cookie"].encode("latin-1")


class TestLoadCookiesSanitizing:
    def test_placeholder_name_is_dropped_with_hint(self, tmp_path, capsys):
        path = write_cookies(tmp_path, [LEGACY_PLACEHOLDER])
        cookies = http_mod.load_cookies(str(path), warn_expired=False)

        assert cookies == {}
        out = capsys.readouterr().out
        assert "自用" in out
        assert "非 ASCII" in out
        assert "z_c0" in out  # 提示里给出正确的键名示例

    def test_chinese_value_is_dropped(self, tmp_path, capsys):
        path = write_cookies(tmp_path, [{"name": "z_c0", "value": "中文值"}])
        cookies = http_mod.load_cookies(str(path), warn_expired=False)

        assert cookies == {}
        assert "z_c0" in capsys.readouterr().out

    def test_valid_cookies_survive_alongside_broken_ones(self, tmp_path, capsys):
        """一条脏数据不能连坐：合法的 z_c0 / d_c0 必须留下。"""
        path = write_cookies(
            tmp_path,
            [
                LEGACY_PLACEHOLDER,
                {"name": "z_c0", "value": "real-z-c0"},
                {"name": "d_c0", "value": "real-d-c0"},
            ],
        )
        cookies = http_mod.load_cookies(str(path), warn_expired=False)

        assert cookies == {"z_c0": "real-z-c0", "d_c0": "real-d-c0"}
        assert "已跳过 1 条" in capsys.readouterr().out

    @pytest.mark.parametrize("bad_name", ["bad name", "a;b", "x=y", "tab\tname", ""])
    def test_illegal_cookie_names_are_dropped(self, tmp_path, bad_name):
        path = write_cookies(tmp_path, [{"name": bad_name, "value": "v"}])
        cookies = http_mod.load_cookies(str(path), warn_expired=False)
        assert cookies == {}

    def test_value_with_control_char_is_dropped(self, tmp_path):
        path = write_cookies(tmp_path, [{"name": "z_c0", "value": "line1\nline2"}])
        assert http_mod.load_cookies(str(path), warn_expired=False) == {}

    def test_dict_style_cookies_are_sanitized(self, tmp_path):
        path = write_cookies(tmp_path, {"z_c0": "ok", "自用": "bad"})
        assert http_mod.load_cookies(str(path), warn_expired=False) == {"z_c0": "ok"}

    def test_clean_file_is_untouched_and_silent(self, tmp_path, capsys):
        payload = [{"name": "z_c0", "value": "ok-1"}, {"name": "d_c0", "value": "ok-2"}]
        path = write_cookies(tmp_path, payload)

        cookies = http_mod.load_cookies(str(path), warn_expired=False)

        assert cookies == {"z_c0": "ok-1", "d_c0": "ok-2"}
        assert capsys.readouterr().out == ""

    def test_expired_cookies_are_still_reported(self, tmp_path, capsys):
        """新增的字符集检查不该把「过期提醒」挤掉。"""
        payload = [{"name": "z_c0", "value": "ok", "expirationDate": 1}]
        path = write_cookies(tmp_path, payload)

        http_mod.load_cookies(str(path), warn_expired=True)

        assert "已过期" in capsys.readouterr().out

    def test_missing_file_returns_empty(self, tmp_path, capsys):
        cookies = http_mod.load_cookies(str(tmp_path / "nope.json"))
        assert cookies == {}
        assert "未找到" in capsys.readouterr().out

    def test_broken_json_returns_empty(self, tmp_path):
        path = tmp_path / "cookies.json"
        path.write_text("{not json", encoding="utf-8")
        assert http_mod.load_cookies(str(path)) == {}


class TestEndToEndRegression:
    """真正要防的是「跑起来就崩」，所以这里直接把 Cookie 头编一遍。"""

    def test_sanitized_cookies_produce_latin1_safe_header(self, tmp_path):
        path = write_cookies(
            tmp_path,
            [LEGACY_PLACEHOLDER, {"name": "z_c0", "value": "real-z-c0"}],
        )
        cookies = http_mod.load_cookies(str(path), warn_expired=False)

        header = cookie_header_for(cookies)

        assert header == "z_c0=real-z-c0"
        assert header.encode("latin-1")  # 不抛异常即通过

    def test_unfiltered_placeholder_would_have_crashed(self):
        """对照：不做过滤时同样的请求必崩 —— 证明过滤不是可有可无的。"""
        with pytest.raises(UnicodeEncodeError):
            cookie_header_for({"自用": "Cookie修sasd11asdasd改这里"}).encode("latin-1")


class TestHeaderGuard:
    def test_default_headers_are_latin1_safe(self):
        for name, value in http_mod.DEFAULT_HEADERS.items():
            value.encode("latin-1")
        for name, value in http_mod.API_HEADERS.items():
            value.encode("latin-1")

    def test_non_ascii_extra_header_is_dropped(self, capsys):
        session = http_mod.create_session(headers={"X-Test": "中文头"})

        assert "X-Test" not in session.headers
        assert "请求头" in capsys.readouterr().out

    def test_valid_extra_header_is_kept(self):
        session = http_mod.create_session(headers={"X-Test": "ok"})
        assert session.headers.get("X-Test") == "ok"


class TestCliEntryGuard:
    def test_unicode_error_is_translated_to_hint(self, monkeypatch, capsys):
        def boom(argv=None):
            raise UnicodeEncodeError("latin-1", "自用=x", 0, 2, "ordinal not in range(256)")

        monkeypatch.setattr(main, "main", boom)

        assert main.run([]) == 2
        out = capsys.readouterr().out
        assert "[编码错误]" in out
        assert "cookies" in out

    def test_normal_return_code_passes_through(self, monkeypatch):
        monkeypatch.setattr(main, "main", lambda argv=None: 0)
        assert main.run([]) == 0


def test_logging_warning_is_emitted(tmp_path, caplog):
    """除了 stdout，日志里也要留痕（用户贴日志求助时能看到原因）。"""
    path = write_cookies(tmp_path, [LEGACY_PLACEHOLDER])

    with caplog.at_level(logging.WARNING):
        http_mod.load_cookies(str(path), warn_expired=False)

    assert any("非 ASCII" in record.message for record in caplog.records)
