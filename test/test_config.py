# -*- coding: utf-8 -*-
"""配置加载与输出路径解析测试。"""

from __future__ import annotations

import json
import pathlib

import pytest

from zhihu_export import config as config_mod


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
        assert not config_mod.is_windows_path("/Users/a1/Zhihu")
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
        config = {"outputPath": "D:/Documents/号主仓库禁删/Zhihu知乎", "os": "windows"}
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
