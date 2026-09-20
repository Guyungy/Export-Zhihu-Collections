# -*- coding: utf-8 -*-
"""配置加载与输出路径解析测试。"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from zhihu_export import config as config_mod
from zhihu_export.collections import parse_collection_id


class TestOsDetect:
    def test_normalize_os_aliases(self):
        assert config_mod.normalize_os("Windows") == "windows"
        assert config_mod.normalize_os("darwin") == "macos"
        assert config_mod.normalize_os("MSYS") == "cygwin"
        assert config_mod.normalize_os("") is None
        assert config_mod.normalize_os("auto") is None

    def test_is_windows_path(self):
        assert config_mod.is_windows_path("D:/Documents/Zhihu")
        assert config_mod.is_windows_path(r"D:\Documents\Zhihu")
        assert not config_mod.is_windows_path("/home/user/Zhihu")
        assert not config_mod.is_windows_path("~/Zhihu")


class TestParseOutputPath:
    def test_expand_user(self):
        path = config_mod.parse_output_path("~/ZhihuExports", "macos")
        assert path is not None
        assert path.is_absolute()
        assert "~" not in str(path)

    def test_empty_returns_none(self):
        assert config_mod.parse_output_path("", "macos") is None
        assert config_mod.parse_output_path(None, "macos") is None

    def test_relative_becomes_absolute(self):
        path = config_mod.parse_output_path("some/sub/dir", "linux")
        assert path is not None and path.is_absolute()


class TestResolveOutputPath:
    def test_plain_string_on_current_os(self, tmp_path):
        config = {"outputPath": str(tmp_path / "out"), "os": ""}
        resolved = config_mod.resolve_output_path(config)
        assert resolved == (tmp_path / "out").resolve()

    def test_windows_path_is_rejected_on_non_windows(self, monkeypatch):
        monkeypatch.setattr(config_mod, "get_current_os", lambda: "macos")
        config = {"outputPath": "D:/Documents/ZhihuExports", "os": "windows"}
        assert config_mod.resolve_output_path(config) is None

    def test_mapping_picks_current_os(self, monkeypatch):
        monkeypatch.setattr(config_mod, "get_current_os", lambda: "macos")
        config = {
            "outputPath": {"windows": "D:/Zhihu", "macos": "~/Documents/ZhihuExports"},
            "os": "windows",
        }
        resolved = config_mod.resolve_output_path(config)
        assert resolved is not None
        assert resolved.name == "ZhihuExports"

    def test_mapping_without_current_os_falls_back_to_default(self, monkeypatch):
        monkeypatch.setattr(config_mod, "get_current_os", lambda: "linux")
        config = {"outputPath": {"windows": "D:/Zhihu"}, "os": "windows"}
        assert config_mod.resolve_output_path(config) is None

    def test_command_line_override_wins(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config_mod, "get_current_os", lambda: "macos")
        config = {"outputPath": "D:/Zhihu", "os": "windows"}
        resolved = config_mod.resolve_output_path(config, override=str(tmp_path / "cli"))
        assert resolved == (tmp_path / "cli").resolve()

    def test_get_search_paths_default(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config_mod, "PROJECT_ROOT", tmp_path)
        base, logs, debug = config_mod.get_search_paths({})
        assert base.name == "downloads"
        assert logs.name == "logs"
        assert debug.name == "debug"

    def test_get_search_paths_custom(self, tmp_path):
        base, logs, debug = config_mod.get_search_paths({"_base_output_path": tmp_path / "custom"})
        assert base == tmp_path / "custom"
        assert logs == tmp_path / "custom" / "logs"
        assert debug == tmp_path / "custom" / "debug"


class TestNumericBounds:
    def test_workers_clamped(self):
        assert config_mod.get_download_workers({}) == config_mod.DEFAULT_DOWNLOAD_WORKERS
        assert config_mod.get_download_workers({"downloadWorkers": 999}) == config_mod.MAX_DOWNLOAD_WORKERS
        assert config_mod.get_download_workers({"downloadWorkers": 0}) == 1
        assert config_mod.get_download_workers({"downloadWorkers": "abc"}) == config_mod.DEFAULT_DOWNLOAD_WORKERS

    def test_delay_clamped(self):
        assert config_mod.get_request_delay({}) == config_mod.DEFAULT_REQUEST_DELAY
        assert config_mod.get_request_delay({"requestDelay": -5}) == 0.0
        assert config_mod.get_request_delay({"requestDelay": 99}) == config_mod.MAX_REQUEST_DELAY
        assert config_mod.get_request_delay({"requestDelay": None}) == config_mod.DEFAULT_REQUEST_DELAY


class TestLoadConfig:
    def test_load_valid_config(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"zhihuUrls": [{"name": "x", "url": "y"}]}, ensure_ascii=False), encoding="utf-8")
        config = config_mod.load_config(str(path))
        assert config["zhihuUrls"] == [{"name": "x", "url": "y"}]

    def test_missing_file_returns_default(self, tmp_path):
        config = config_mod.load_config(str(tmp_path / "nope.json"))
        assert config == config_mod.default_config()

    def test_broken_json_returns_default(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text("{ not json", encoding="utf-8")
        config = config_mod.load_config(str(path))
        assert config["zhihuUrls"] == []

    def test_legacy_zhihu_urls_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config_mod, "PROJECT_ROOT", tmp_path)
        (tmp_path / "zhihuUrls.json").write_text(
            json.dumps([{"name": "旧配置", "url": "https://www.zhihu.com/collection/1"}], ensure_ascii=False),
            encoding="utf-8",
        )
        config = config_mod.load_config()
        assert config["zhihuUrls"][0]["name"] == "旧配置"
        assert config["openCollection"] is False


class TestExampleConfig:
    """仓库里只保留示例配置，它必须「复制过去就能跑」。

    背景：这个文件曾经叫 config_examples.json，顶层是 ``default_example`` / ``macos_example``
    这类多场景字典 —— 照 README 抄 ``cp`` 过来会得到一个 ``zhihuUrls`` 为空的配置，
    用户第一步就卡住。示例文件必须是**合法且可用**的单一配置。
    """

    @pytest.fixture()
    def example(self):
        path = config_mod.PROJECT_ROOT / config_mod.EXAMPLE_CONFIG_FILENAME
        assert path.exists(), "仓库必须提供 %s" % config_mod.EXAMPLE_CONFIG_FILENAME
        return path

    def test_is_usable_as_is(self, example):
        config = config_mod.load_config(str(example))
        urls = config.get("zhihuUrls") or []
        assert urls, "示例配置的 zhihuUrls 不能为空，否则用户复制后什么也导不出来"
        for entry in urls:
            assert entry.get("name"), "示例里每条收藏夹都要有 name（用于目录命名）"
            assert parse_collection_id(entry["url"]), "示例里的 url 必须能解析出收藏夹 ID"

    def test_does_not_ship_real_collection_ids(self, example):
        """示例是公开仓库的一部分，不能把你自己的收藏夹清单写进去。"""
        config = config_mod.load_config(str(example))
        assert config.get("outputPath") == "", "示例里的 outputPath 要留空，避免带上个人路径"
        assert config.get("os") == "", "示例里的 os 要留空（自动检测）"
        ids = {parse_collection_id(e["url"]) for e in config["zhihuUrls"]}
        assert ids, "至少要有一条示例收藏夹"
        # 真实收藏夹 ID 是 9 位且以 3/6/7/8/9 开头的知乎数字 ID；
        # 示例统一用 1234567xx 这类占位号，避免暴露个人收藏目录
        assert all(i.startswith("1234567") for i in ids), "示例收藏夹 ID 必须是占位值"

    def test_stays_in_sync_with_code_defaults(self, example):
        config = config_mod.load_config(str(example))
        assert config["downloadWorkers"] == config_mod.DEFAULT_DOWNLOAD_WORKERS
        assert config["imageWorkers"] == config_mod.DEFAULT_IMAGE_WORKERS
        assert config["requestDelay"] == config_mod.DEFAULT_REQUEST_DELAY
        assert config["pageTimeout"] == config_mod.DEFAULT_PAGE_TIMEOUT
        assert config["longPageTimeout"] == config_mod.DEFAULT_LONG_PAGE_TIMEOUT
        assert config["fetchRetries"] == config_mod.DEFAULT_FETCH_RETRIES
        assert config["apiFallback"] is True

    def test_every_option_is_documented(self, example):
        """示例里出现的键必须在 README 的配置表里有一条 —— 免得加了字段没人知道。"""
        readme = (config_mod.PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        for key in config_mod.load_config(str(example)):
            assert "`%s`" % key in readme, "README 的配置表缺了 `%s`" % key


class TestDocsPointAtRealFiles:
    """文档里的 ``cp <file> config.json`` 抄错文件名会让新用户第一步就失败（真发生过）。"""

    CP_PATTERN = re.compile(r"cp\s+([\w./-]+\.json)\s+config\.json")

    def test_cp_source_files_exist(self):
        docs = [
            path
            for name in ("README.md", "CONTRIBUTING.md", "claude.md", "CHANGELOG.md")
            if (path := config_mod.PROJECT_ROOT / name).exists()
        ]
        checked = 0
        for doc in docs:
            for src in self.CP_PATTERN.findall(doc.read_text(encoding="utf-8")):
                checked += 1
                assert (config_mod.PROJECT_ROOT / src).exists(), (
                    "%s 里写了 `cp %s config.json`，但仓库里没有 %s" % (doc.name, src, src)
                )
        assert checked, "没匹配到任何 cp 示例，正则可能失效了"

    def test_config_json_itself_is_ignored_not_committed(self):
        """个人配置不能进仓库：两个真实配置文件要被排除，示例文件必须仍可提交。"""
        ignore = (config_mod.PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        # 只看生效的规则行，注释里提到文件名不算（注释正是用来解释这个规则的）
        rules = [
            line.strip()
            for line in ignore.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        assert "/config.json" in rules, ".gitignore 必须精确排除 /config.json"
        assert "/%s" % config_mod.LEGACY_URLS_FILENAME in rules, (
            "旧版 %s 里同样是个人收藏夹清单，也要排除" % config_mod.LEGACY_URLS_FILENAME
        )
        assert not any(config_mod.EXAMPLE_CONFIG_FILENAME in rule for rule in rules), (
            "规则行不能把 %s 一起忽略" % config_mod.EXAMPLE_CONFIG_FILENAME
        )
