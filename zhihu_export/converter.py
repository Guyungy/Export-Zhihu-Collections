# -*- coding: utf-8 -*-
"""HTML → Markdown 转换（Obsidian 风格）以及正文图片下载。

主要能力：

* :class:`ImageDownloader` —— 图片下载 + 去重缓存 + 并发预取 + 文件名冲突规避
* :class:`ObsidianStyleConverter` —— 图片转 ``![[文件名]]`` 内嵌语法，
  保留脚注 / 参考文献链接，链接卡片降级为纯文本
* :func:`sanitize_content` —— 转换前的 DOM 清理（脚本样式、占位图、链接卡片、邮箱误判）
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import pathlib
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, Optional

from markdownify import ATX, MarkdownConverter

try:
    from markdownify import chomp
except ImportError:  # pragma: no cover - markdownify < 1.1
    def chomp(text: str):
        prefix = " " if text and text[0] == " " else ""
        suffix = " " if text and text[-1] == " " else ""
        return prefix, suffix, text.strip() if text else ""


#: 文件名里可能出现的非法字符（主要照顾 Windows）
_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')
_REPEATED_UNDERSCORES = re.compile(r"_{2,}")
_EXT_RE = re.compile(r"\.[A-Za-z0-9]{2,5}$")

DEFAULT_IMAGE_EXT = ".jpg"
MIME_EXT_MAP = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
    "image/avif": ".avif",
}


def safe_filename(name: str) -> str:
    """把任意字符串清理成可用于文件名的形式。"""
    cleaned = _ILLEGAL_FILENAME_CHARS.sub("_", str(name or "")).strip().strip(".")
    cleaned = _REPEATED_UNDERSCORES.sub("_", cleaned)  # 连续非法字符只留一个下划线
    return cleaned or "unnamed"


class ImageDownloader:
    """正文图片下载器。

    * 同一个 ``src`` 只会下载一次（缓存）
    * 同名不同图时自动加 URL 摘要后缀，避免互相覆盖
    * 单张图片失败不会影响其它图片与整篇文章
    """

    def __init__(self, session, assets_dir, max_workers: int = 4):
        self.session = session
        self.assets_dir = pathlib.Path(assets_dir)
        self.max_workers = max(1, int(max_workers or 1))
        self._by_url: Dict[str, Optional[str]] = {}
        self._name_owner: Dict[str, str] = {}
        self._lock = threading.Lock()
        self.stats = {"downloaded": 0, "cached": 0, "failed": 0, "bytes": 0}

    # ------------------------------------------------------------------ 内部
    def _resolve_name(self, url: str, content_type: str = "") -> str:
        """为图片决定一个在 assets 目录里唯一的文件名。"""
        raw_name = url.split("?")[0].split("/")[-1]
        raw_name = safe_filename(raw_name)
        if not _EXT_RE.search(raw_name):
            ext = MIME_EXT_MAP.get((content_type or "").split(";")[0].strip().lower())
            if not ext:
                ext = mimetypes.guess_extension(content_type or "") or DEFAULT_IMAGE_EXT
            raw_name = (raw_name or "image") + ext

        owner = self._name_owner.get(raw_name)
        if owner is None or owner == url:
            self._name_owner[raw_name] = url
            return raw_name

        digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
        stem, ext = os.path.splitext(raw_name)
        unique = "%s_%s%s" % (stem, digest, ext)
        self._name_owner[unique] = url
        return unique

    def _download(self, url: str) -> Optional[str]:
        if not url or url.startswith("data:"):
            return None
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            content = response.content
            if not content:
                raise ValueError("响应内容为空")

            with self._lock:
                filename = self._resolve_name(url, response.headers.get("Content-Type", ""))

            self.assets_dir.mkdir(parents=True, exist_ok=True)
            target = self.assets_dir / filename
            with open(target, "wb") as fp:
                fp.write(content)

            self.stats["downloaded"] += 1
            self.stats["bytes"] += len(content)
            return filename
        except Exception as exc:  # noqa: BLE001 - 单张图片失败继续处理其它图片
            self.stats["failed"] += 1
            logging.warning("图片下载失败: %s（%s）", url, exc)
            return None

    # ------------------------------------------------------------------ 对外
    def fetch(self, url: str) -> Optional[str]:
        """下载（或命中缓存）一张图片，返回本地文件名；失败返回 ``None``。"""
        if not url or url.startswith("data:"):
            return None
        with self._lock:
            if url in self._by_url:
                self.stats["cached"] += 1
                return self._by_url[url]

        filename = self._download(url)
        with self._lock:
            self._by_url[url] = filename
        return filename

    def prefetch(self, urls: Iterable[str]) -> Dict[str, Optional[str]]:
        """并发预取一批图片，把并发花在真正耗时的网络 IO 上。"""
        pending = []
        with self._lock:
            for url in dict.fromkeys(urls):
                if url and not url.startswith("data:") and url not in self._by_url:
                    pending.append(url)

        if not pending:
            return dict(self._by_url)

        workers = min(self.max_workers, len(pending))
        logging.debug("并发预取 %d 张图片（%d 线程）", len(pending), workers)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(self.fetch, url): url for url in pending}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001
                    with self._lock:
                        self._by_url[url] = None
                    logging.warning("预取图片异常: %s（%s）", url, exc)

        return dict(self._by_url)


class ObsidianStyleConverter(MarkdownConverter):
    """把知乎正文 HTML 转成 Obsidian 兼容的 Markdown。

    * 图片：下载到 ``assets/``，正文写成 ``![[文件名]]``，后面跟一行原图说明文字
    * 脚注 / 参考文献：保留 ``[^1]`` 形式
    * 链接卡片：降级为卡片标题纯文本
    """

    def __init__(self, image_downloader: Optional[ImageDownloader] = None, **options):
        options.setdefault("heading_style", ATX)
        super().__init__(**options)
        self.image_downloader = image_downloader
        self.stats = {"images": 0, "image_failures": 0}

    # markdownify 不同大版本对 super() 的调用签名不同，这里做一次兼容收敛
    def _super(self, name: str, el, text, parent_tags):
        method = getattr(super(), name)
        try:
            return method(el, text, parent_tags)
        except TypeError:  # pragma: no cover - markdownify < 1.1
            return method(el, text, bool(parent_tags))

    def convert_img(self, el, text, parent_tags):
        """图片：落盘并改成 Obsidian 内嵌语法；失败则保留远程链接。"""
        alt = (el.attrs.get("alt") or "").strip()
        src = (el.attrs.get("src") or "").strip()

        if not src or src.startswith("data:"):
            return ""

        filename = self.image_downloader.fetch(src) if self.image_downloader else None
        if filename:
            self.stats["images"] += 1
            return "![[%s]]\n(%s)\n\n" % (filename, alt)

        self.stats["image_failures"] += 1
        return "![%s](%s)\n\n" % (alt, src)

    def convert_a(self, el, text, parent_tags):
        """链接：保留参考文献 / 脚注语义，其余交给父类。"""
        attrs = el.attrs or {}

        aria_labelledby = attrs.get("aria-labelledby") or ""
        if "ref" in aria_labelledby:
            return (text or "").replace("[", "[^")

        href = attrs.get("href") or ""
        classes = attrs.get("class") or []
        if "data-reference-link" in attrs or "ReferenceList-backLink" in classes:
            ref_key = href[5] if len(href) > 5 else ""
            return "[^{}]: ".format(ref_key)

        return self._super("convert_a", el, text, parent_tags)

    def convert_li(self, el, text, parent_tags):
        """列表项：脚注回链所在的 ``<li>`` 不输出列表符号。"""
        try:
            is_back_link = el.find("a", {"aria-label": "back"}) is not None
        except Exception:  # noqa: BLE001 - el 可能不是 Tag
            is_back_link = False

        if is_back_link:
            stripped = (text or "").strip()
            return "%s\n" % stripped if stripped else ""

        return self._super("convert_li", el, text, parent_tags)


def markdownify(html: str, image_downloader: Optional[ImageDownloader] = None, **options) -> str:
    """便捷入口：把 HTML 字符串转成 Markdown。"""
    return ObsidianStyleConverter(image_downloader=image_downloader, **options).convert(html)


def sanitize_content(content) -> None:
    """就地清理正文 DOM，避免脏节点影响转换结果。

    * 去掉 ``<style>`` 与 svg 占位图
    * 链接卡片 ``a.LinkCard`` 只保留卡片标题
    * 正文里的邮箱被知乎包成 ``mailto:`` 链接，会破坏 Markdown 转换，降级为纯文本
    """
    if content is None or not hasattr(content, "find_all"):
        return

    for el in content.find_all("style"):
        el.extract()

    for el in content.select('img[src*="data:image/svg+xml"]'):
        el.extract()

    for el in content.find_all("a"):
        classes = el.get("class") or []
        if isinstance(classes, list) and classes and classes[0] == "LinkCard":
            card_name = el.get("data-text") or el.get("href")
            el.string = card_name

        href = el.get("href") or ""
        if href.startswith("mailto:"):
            el.name = "span"
            if el.has_attr("href"):
                del el["href"]


def prefetch_content_images(content, downloader: Optional[ImageDownloader]) -> None:
    """并发预取正文里出现的所有图片。"""
    if downloader is None or content is None or not hasattr(content, "find_all"):
        return
    urls = [img.get("src") for img in content.find_all("img") if img.get("src")]
    if urls:
        downloader.prefetch(urls)


def html_template(data) -> str:
    """把正文片段包成完整 HTML，供 markdownify 解析。"""
    return """
        <html>
        <head>
        </head>
        <body>
        %s
        </body>
        </html>
        """ % data
