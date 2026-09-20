# -*- coding: utf-8 -*-
"""main.py 端到端流程测试（离线，全部落在 tmp 目录）。"""

from __future__ import annotations

import json
import logging
import pathlib
from typing import List

import pytest
from helpers import FakeResponse, FakeSession, collection_items_payload, read_fixture

import main
from zhihu_export.collections import CollectionItem
from zhihu_export.http import RateLimiter

ANSWER_URL = "https://www.zhihu.com/question/1/answer/2"
POST_URL = "https://zhuanlan.zhihu.com/p/386395767"


@pytest.fixture()
def isolated_env(tmp_path, monkeypatch):
    """把输出目录、日志、全局状态都隔离到 tmp_path。"""
    previous_handlers = logging.getLogger().handlers[:]

    monkeypatch.setattr(main, "base_output_path", tmp_path)
    monkeypatch.setattr(
        main,
        "config",
        {
            "outputPath": str(tmp_path),
            "downloadWorkers": 2,
            "imageWorkers": 2,
            "requestDelay": 0,
        },
    )
    monkeypatch.setattr(main, "request_limiter", RateLimiter(0))
    main.processing_log = []

    main.reconfigure_logging()
    try:
        yield tmp_path
    finally:
        logging.getLogger().handlers[:] = previous_handlers


@pytest.fixture(autouse=True)
def never_read_real_config(monkeypatch):
    """这些测试必须自给自足，不能读你本机真实的 ``config.json``。

    仓库里已经不含 ``config.json``（它含个人收藏夹清单，已 gitignore），
    所以 CI 上读到的是空配置 —— 若用例依赖真实配置，就会「本地绿、CI 红」。
    需要别的配置时，在用例内部再 patch 一次即可覆盖本夹具。
    """
    monkeypatch.setattr(
        main.config_mod,
        "load_config",
        lambda path=None: {
            "zhihuUrls": [
                {"name": "技术-效率工具", "url": "https://www.zhihu.com/collection/123450010"}
            ]
        },
    )


def _session_for(html_path: str, url: str, images: bool = True) -> FakeSession:
    html = read_fixture(html_path)

    def handler(request_url: str) -> FakeResponse:
        if request_url == url:
            return FakeResponse(text=html, url=url)
        if images and "zhimg.com" in request_url:
            return FakeResponse(content=b"\xff\xd8\xff img", headers={"Content-Type": "image/jpeg"})
        return None

    return FakeSession(handler)


class TestHelpers:
    def test_is_article_already_downloaded(self, tmp_path):
        path = tmp_path / "a.md"
        assert not main.is_article_already_downloaded(str(path), "u")

        path.write_text("", encoding="utf-8")
        assert not main.is_article_already_downloaded(str(path), "u")  # 空文件视为未下载

        path.write_text("> u\n\n正文", encoding="utf-8")
        assert main.is_article_already_downloaded(str(path), "u")

        path.write_text("> other\n\n正文", encoding="utf-8")
        assert not main.is_article_already_downloaded(str(path), "u")

    def test_build_reserved_file_path_dedupes(self, tmp_path):
        reserved = set()
        first = main.build_reserved_file_path(str(tmp_path), "同名标题", "https://www.zhihu.com/question/1/answer/1", reserved)
        second = main.build_reserved_file_path(str(tmp_path), "同名标题", "https://www.zhihu.com/question/1/answer/2", reserved)
        third = main.build_reserved_file_path(str(tmp_path), "同名标题", "https://www.zhihu.com/question/1/answer/3", reserved)
        assert len({first, second, third}) == 3

    def test_get_output_path_uses_collection_name(self, isolated_env):
        assert main.get_output_path("技术-效率工具") == str(isolated_env / "技术-效率工具")

    def test_select_collections_by_name_and_id(self):
        collections = [
            {"name": "技术-效率工具", "url": "https://www.zhihu.com/collection/123450010"},
            {"name": "赚钱-金融市场", "url": "https://www.zhihu.com/collection/123450018"},
        ]
        assert len(main._select_collections(collections, [])) == 2
        assert [c["name"] for c in main._select_collections(collections, ["123450018"])] == ["赚钱-金融市场"]
        assert [c["name"] for c in main._select_collections(collections, ["技术"])] == ["技术-效率工具"]
        assert main._select_collections(collections, ["不存在的"]) == []

    def test_arg_parser_defaults(self):
        args = main.build_arg_parser().parse_args([])
        assert args.only == []
        assert args.workers is None
        assert args.list is False


class TestProcessCollection:
    def test_full_answer_export(self, isolated_env, monkeypatch):
        monkeypatch.setattr(main, "session", _session_for("answer_page.html", ANSWER_URL))
        monkeypatch.setattr(
            main,
            "fetch_collection_items",
            lambda *a, **kw: [CollectionItem(title="示例回答", url=ANSWER_URL, type="answer")],
        )

        main.process_single_collection("测试收藏夹", "https://www.zhihu.com/collection/1")

        collection_dir = isolated_env / "测试收藏夹"
        md_files = list(collection_dir.glob("*.md"))
        assert len(md_files) == 1
        content = md_files[0].read_text(encoding="utf-8")
        assert content.startswith("> %s" % ANSWER_URL)
        assert "![[v2-demo-pic_1440w.jpg]]" in content
        assert (collection_dir / "assets" / "v2-demo-pic_1440w.jpg").exists()

        entries = main.processing_log[0]["list"]
        assert entries[0]["status"] == "正常下载"
        assert main.summarize()["downloaded"] == 1

    def test_rerun_skips_existing(self, isolated_env, monkeypatch):
        monkeypatch.setattr(main, "session", _session_for("answer_page.html", ANSWER_URL))
        monkeypatch.setattr(
            main,
            "fetch_collection_items",
            lambda *a, **kw: [CollectionItem(title="示例回答", url=ANSWER_URL, type="answer")],
        )

        main.process_single_collection("测试收藏夹", "https://www.zhihu.com/collection/1")
        main.process_single_collection("测试收藏夹", "https://www.zhihu.com/collection/1")

        second_run = main.processing_log[1]["list"]
        assert second_run[0]["status"] == "文章已存在,跳过下载"
        assert len(list((isolated_env / "测试收藏夹").glob("*.md"))) == 1

    def test_post_url_routed_to_post_parser(self, isolated_env, monkeypatch):
        called = {}

        def fake_post(url):
            called["url"] = url
            return main.html_template("<p>专栏内容</p>")

        monkeypatch.setattr(main, "session", FakeSession(lambda u: FakeResponse()))
        monkeypatch.setattr(main, "get_single_post_content", fake_post)
        monkeypatch.setattr(main, "get_single_answer_content", lambda url: pytest.fail("不应走回答解析"))
        monkeypatch.setattr(
            main,
            "fetch_collection_items",
            lambda *a, **kw: [CollectionItem(title="专栏标题", url=POST_URL, type="article")],
        )

        main.process_single_collection("专栏夹", "https://www.zhihu.com/collection/2")
        assert called["url"] == POST_URL
        assert main.summarize()["downloaded"] == 1

    def test_failure_does_not_write_placeholder_file(self, isolated_env, monkeypatch):
        monkeypatch.setattr(main, "session", FakeSession(lambda u: FakeResponse()))
        monkeypatch.setattr(main, "get_single_answer_content", lambda url: main.FETCH_FAILED)
        monkeypatch.setattr(
            main,
            "fetch_collection_items",
            lambda *a, **kw: [CollectionItem(title="打不开的回答", url=ANSWER_URL, type="answer")],
        )

        main.process_single_collection("失败收藏夹", "https://www.zhihu.com/collection/3")

        assert list((isolated_env / "失败收藏夹").glob("*.md")) == []
        entry = main.processing_log[0]["list"][0]
        assert entry["status"].startswith("文章下载失败")
        assert main.summarize()["failed"] == 1

    def test_empty_collection_is_recorded(self, isolated_env, monkeypatch):
        monkeypatch.setattr(main, "session", FakeSession(lambda u: FakeResponse()))
        monkeypatch.setattr(main, "fetch_collection_items", lambda *a, **kw: [])

        main.process_single_collection("空收藏夹", "https://www.zhihu.com/collection/4")
        assert main.processing_log[0]["list"] == []

    def test_images_can_be_skipped(self, isolated_env, monkeypatch):
        monkeypatch.setattr(main, "config", dict(main.config, skipImages=True))
        monkeypatch.setattr(main, "session", _session_for("answer_page.html", ANSWER_URL, images=False))
        monkeypatch.setattr(
            main,
            "fetch_collection_items",
            lambda *a, **kw: [CollectionItem(title="不下载图片", url=ANSWER_URL, type="answer")],
        )

        main.process_single_collection("无图收藏夹", "https://www.zhihu.com/collection/5")
        content = next((isolated_env / "无图收藏夹").glob("*.md")).read_text(encoding="utf-8")
        assert "![[v2-demo-pic_1440w.jpg]]" not in content
        assert "![示意图](https://pic1.zhimg.com/v2-demo-pic_1440w.jpg)" in content

    def test_save_processing_log_writes_json(self, isolated_env, monkeypatch):
        monkeypatch.setattr(main, "session", _session_for("answer_page.html", ANSWER_URL))
        monkeypatch.setattr(
            main,
            "fetch_collection_items",
            lambda *a, **kw: [CollectionItem(title="示例回答", url=ANSWER_URL, type="answer")],
        )
        main.process_single_collection("测试收藏夹", "https://www.zhihu.com/collection/1")

        log_path = main.save_processing_log()
        assert pathlib.Path(log_path).exists()
        data = json.loads(pathlib.Path(log_path).read_text(encoding="utf-8"))
        assert data[0]["name"] == "测试收藏夹"


class TestCli:
    def test_list_mode(self, isolated_env, monkeypatch, capsys):
        def handler(url: str) -> FakeResponse:
            if "collections/mine" in url:
                return FakeResponse(text=read_fixture("mine_collections_page.html"))
            return FakeResponse(json_data={"paging": {"totals": 7}})

        monkeypatch.setattr(main, "build_session", lambda cookies=None: FakeSession(handler))
        monkeypatch.setattr(main, "load_cookies", lambda: {"z_c0": "fake"})

        exit_code = main.main(["--output", str(isolated_env), "--list", "--only", "123450010"])
        assert exit_code == 0
        output = capsys.readouterr().out
        assert "技术-效率工具" in output
        assert "7 条" in output

    def test_open_collection_mode_asks_for_fetch(self, isolated_env, monkeypatch, capsys):
        monkeypatch.setattr(main, "load_cookies", lambda: {})
        monkeypatch.setattr(main.config_mod, "load_config", lambda path=None: {"openCollection": True})

        exit_code = main.main(["--output", str(isolated_env)])
        assert exit_code == 1
        assert "fetch_collections.py" in capsys.readouterr().out

    def test_empty_collections_exits_nonzero(self, isolated_env, monkeypatch, capsys):
        monkeypatch.setattr(main, "load_cookies", lambda: {})
        monkeypatch.setattr(main.config_mod, "load_config", lambda path=None: {"zhihuUrls": []})

        assert main.main(["--output", str(isolated_env)]) == 1
        assert "没有找到要处理的收藏夹配置" in capsys.readouterr().out
