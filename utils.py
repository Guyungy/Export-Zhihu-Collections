# -*- coding: utf-8 -*-
"""文件名与路径相关的工具函数。"""

from __future__ import annotations

import re

#: 各系统文件名里的非法字符统一替换成空格
_ILLEGAL_CHARS = re.compile(r'[\/\\"<>|*]')
_WHITESPACE = re.compile(r"\s+")
_FULLWIDTH_COLON_SPACE = re.compile(r"：\s+")

#: 单个文件名的字节上限（大多数文件系统 255 字节，留出扩展名与去重后缀的余量）
MAX_FILENAME_BYTES = 220


def filter_title_str(title: str) -> str:
    """把文章标题清理成安全的文件名（不含扩展名）。

    * 替换 ``/ \\ " < > | *`` 等非法字符
    * 半角 ``?`` / ``:` 换成全角，保留中文标题的可读性
    * 合并空白、去掉结尾的点和空格（Windows 上会报错）
    * 按字节截断，避免「文件名过长」导致整篇导出失败
    """
    text = str(title or "")
    text = _ILLEGAL_CHARS.sub(" ", text)
    text = text.replace("?", "？").replace(":", "：")
    text = _WHITESPACE.sub(" ", text).strip()
    text = _FULLWIDTH_COLON_SPACE.sub("：", text)  # 「答： 测试」→「答：测试」
    text = text.rstrip(". ")

    if not text:
        return "未命名"

    return truncate_bytes(text, MAX_FILENAME_BYTES)


def truncate_bytes(text: str, max_bytes: int) -> str:
    """按 UTF-8 字节数截断字符串，不会把一个汉字截成半个。"""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text

    truncated = encoded[:max_bytes]
    # 逐字节回退，直到能正确解码
    while truncated:
        try:
            return truncated.decode("utf-8")
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return "未命名"
