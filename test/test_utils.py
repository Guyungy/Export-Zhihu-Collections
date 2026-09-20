# -*- coding: utf-8 -*-
"""文件名清理测试。"""

from __future__ import annotations

from utils import filter_title_str, truncate_bytes


def test_replaces_illegal_chars():
    assert "/" not in filter_title_str("a/b")
    assert "\\" not in filter_title_str("a\\b")
    assert '"' not in filter_title_str('a"b')
    assert "*" not in filter_title_str("a*b")


def test_full_width_punctuation_kept_readable():
    assert filter_title_str("怎么办?") == "怎么办？"
    assert filter_title_str("答: 测试") == "答：测试"


def test_collapses_whitespace_and_trailing_dots():
    assert filter_title_str("a   b ...") == "a b"
    assert filter_title_str("标题。") == "标题。"


def test_empty_title_falls_back():
    assert filter_title_str("") == "未命名"
    assert filter_title_str("///") == "未命名"


def test_long_chinese_title_truncated_by_bytes():
    title = "这是一个很长的标题" * 60
    result = filter_title_str(title)
    assert len(result.encode("utf-8")) <= 220
    # 不能把汉字截成半个（能正确编码解码即说明没问题）
    assert result.encode("utf-8").decode("utf-8") == result


def test_truncate_bytes_keeps_short_text():
    assert truncate_bytes("短标题", 220) == "短标题"
    assert len(truncate_bytes("啊" * 100, 10).encode("utf-8")) <= 10
