#!/usr/bin/env python3
"""检查 README 里的内部锚点链接是否都能解析到真实标题。

GitHub 生成标题锚点的规则容易踩坑：emoji 会被丢掉，但 emoji 后面的变体选择符
（U+FE0F，比如 ⚙️ 里的那个）会**留在锚点里**。于是：

    ## ⚙️ 配置说明   →  #️-配置说明   （注意 # 后面那个不可见字符）
    ## ✨ 特性一览   →  #-特性一览

少写一个不可见字符，链接就静默失效。这个脚本把规则复刻出来做自检。

用法::

    python tools/check_readme_anchors.py [README 路径...]

退出码非 0 表示存在失效锚点。
"""

from __future__ import annotations

import pathlib
import re
import sys
import urllib.parse

VS16 = "\ufe0f"  # emoji 变体选择符，GitHub 的锚点里会保留它


def slug(title: str) -> str:
    """复刻 GitHub 的标题锚点规则：去 emoji / 标点，保留 VS16，空格转连字符。"""
    kept = []
    for ch in title.strip().lower():
        if ch == VS16 or ch.isalnum() or ch in " -_":
            kept.append(ch)
    return re.sub(r"\s", "-", "".join(kept))


def collect_anchors(text: str) -> dict:
    """返回 {锚点: 原始标题}。"""
    anchors = {}
    for line in text.splitlines():
        match = re.match(r"^#{1,4}\s+(.*)$", line)
        if match:
            anchors[slug(match.group(1))] = match.group(1)
    return anchors


def check(path: pathlib.Path) -> int:
    text = path.read_text(encoding="utf-8")
    anchors = collect_anchors(text)
    links = dict.fromkeys(re.findall(r"\]\((#[^)]*)\)", text))

    broken = []
    for href in links:
        fragment = urllib.parse.unquote(href[1:])
        if fragment and fragment not in anchors:
            broken.append((href, fragment))

    print("%s: %d 个标题, %d 个内部链接" % (path, len(anchors), len(links)))
    if not broken:
        print("  ✅ 全部可解析")
        return 0

    for href, fragment in broken:
        print("  ❌ %s（锚点 %r 不存在）" % (href, fragment))
        for candidate in anchors:
            if candidate.endswith("-" + fragment.lstrip("-")):
                print("     也许想写: #%s" % candidate)
    return 1


def main(argv) -> int:
    targets = [pathlib.Path(p) for p in argv[1:]] or [pathlib.Path("README.md")]
    missing = [p for p in targets if not p.exists()]
    for path in missing:
        print("找不到文件: %s" % path, file=sys.stderr)
    if missing:
        return 2
    return max((check(p) for p in targets), default=0)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
