# -*- coding:utf-8 -*-
import argparse
import hashlib
import json
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from markdownify import MarkdownConverter
from requests import Response
from tqdm import tqdm

from utils import filter_title_str

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/61.0.3163.100 Safari/537.36"
    ),
    "Connection": "keep-alive",
    "Accept": "text/html,application/json,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.8",
}


@dataclass
class ExportOptions:
    collection_url: str
    output_dir: str
    workers: int
    retries: int
    timeout: int
    min_delay: float
    max_delay: float
    limit: Optional[int]
    resume: bool
    include_images: bool
    list_only: bool
    interactive: bool


@dataclass
class CollectionItem:
    title: str
    url: str
    content_type: str


@dataclass
class ExportResult:
    title: str
    url: str
    status: str
    reason: str = ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="知乎文章剪藏")
    parser.add_argument("collection_url", nargs="?", help="收藏夹网址（支持公开和私密收藏夹）")
    parser.add_argument("--workers", type=int, default=4, help="并发线程数，默认 4")
    parser.add_argument("--retries", type=int, default=3, help="单个请求失败后的重试次数，默认 3")
    parser.add_argument("--timeout", type=int, default=15, help="HTTP 请求超时时间，默认 15 秒")
    parser.add_argument("--min-delay", type=float, default=0.3, help="每篇文章处理完成后的最小随机等待秒数")
    parser.add_argument("--max-delay", type=float, default=1.2, help="每篇文章处理完成后的最大随机等待秒数")
    parser.add_argument("--limit", type=int, default=None, help="只导出前 N 篇")
    parser.add_argument("--output", default=None, help="导出目录，默认 ./downloads/剪藏")
    parser.add_argument("--no-resume", action="store_true", help="不跳过已存在的 markdown 文件")
    parser.add_argument("--no-images", action="store_true", help="不下载图片资源")
    parser.add_argument("--list-only", action="store_true", help="仅列出收藏内容，不执行导出")
    parser.add_argument("--interactive", action="store_true", help="进入交互模式")
    return parser


def load_cookies() -> dict:
    try:
        with open("cookies.json", "r", encoding="utf-8") as file:
            cookies_list = json.load(file)
    except FileNotFoundError:
        print("未找到 cookies.json，将使用无登录模式访问（部分内容可能无法获取）")
        return {}
    except json.JSONDecodeError as exc:
        print(f"cookies.json 解析失败：{exc}，将使用无登录模式访问")
        return {}

    cookies_dict = {}
    for cookie in cookies_list:
        name = cookie.get("name")
        value = cookie.get("value")
        if name and value is not None:
            cookies_dict[name] = value
    return cookies_dict


def default_output_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "downloads", "剪藏")


def prompt_text(label: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or (default or "")


def prompt_int(label: str, default: int, minimum: int = 1) -> int:
    while True:
        raw = prompt_text(label, str(default))
        try:
            value = int(raw)
            if value < minimum:
                raise ValueError
            return value
        except ValueError:
            print(f"请输入不小于 {minimum} 的整数")


def prompt_float(label: str, default: float, minimum: float = 0.0) -> float:
    while True:
        raw = prompt_text(label, str(default))
        try:
            value = float(raw)
            if value < minimum:
                raise ValueError
            return value
        except ValueError:
            print(f"请输入不小于 {minimum} 的数字")


def prompt_yes_no(label: str, default: bool) -> bool:
    default_label = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{label} [{default_label}]: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("请输入 y 或 n")


def gather_options(args: argparse.Namespace) -> ExportOptions:
    interactive = args.interactive or not args.collection_url
    output_dir = args.output or default_output_dir()

    if not interactive:
        return ExportOptions(
            collection_url=args.collection_url,
            output_dir=output_dir,
            workers=max(1, args.workers),
            retries=max(1, args.retries),
            timeout=max(1, args.timeout),
            min_delay=max(0.0, args.min_delay),
            max_delay=max(args.min_delay, args.max_delay),
            limit=args.limit if args.limit and args.limit > 0 else None,
            resume=not args.no_resume,
            include_images=not args.no_images,
            list_only=args.list_only,
            interactive=interactive,
        )

    collection_url = prompt_text("收藏夹 URL", args.collection_url or "")
    while not collection_url:
        collection_url = prompt_text("收藏夹 URL", args.collection_url or "")

    workers = prompt_int("并发线程数", max(1, args.workers))
    retries = prompt_int("重试次数", max(1, args.retries))
    timeout = prompt_int("请求超时（秒）", max(1, args.timeout))
    min_delay = prompt_float("最小随机等待（秒）", max(0.0, args.min_delay))
    max_delay = prompt_float("最大随机等待（秒）", max(min_delay, args.max_delay), minimum=min_delay)
    limit = prompt_text("仅导出前 N 篇（留空表示全部）", str(args.limit) if args.limit else "")
    output_dir = prompt_text("输出目录", output_dir)
    resume = prompt_yes_no("跳过已存在文件", not args.no_resume)
    include_images = prompt_yes_no("下载图片", not args.no_images)
    list_only = prompt_yes_no("仅列出收藏内容，不导出", args.list_only)

    return ExportOptions(
        collection_url=collection_url,
        output_dir=output_dir,
        workers=workers,
        retries=retries,
        timeout=timeout,
        min_delay=min_delay,
        max_delay=max_delay,
        limit=int(limit) if limit.strip().isdigit() and int(limit) > 0 else None,
        resume=resume,
        include_images=include_images,
        list_only=list_only,
        interactive=interactive,
    )


class ZhihuClient:
    def __init__(self, cookies: dict, timeout: int, retries: int):
        self.cookies = cookies
        self.timeout = timeout
        self.retries = retries
        self._thread_local = threading.local()

    def _get_session(self) -> requests.Session:
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(DEFAULT_HEADERS)
            session.cookies.update(self.cookies)
            self._thread_local.session = session
        return session

    def get(self, url: str, **kwargs) -> Response:
        last_exc = None
        for attempt in range(1, self.retries + 1):
            try:
                response = self._get_session().get(url, timeout=self.timeout, **kwargs)
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_exc = exc
                if attempt == self.retries:
                    break
                time.sleep(min(2 ** (attempt - 1), 5))
        raise RuntimeError(f"请求失败: {url} ({last_exc})")

    def get_json(self, url: str) -> dict:
        return self.get(url).json()

    def get_text(self, url: str) -> str:
        return self.get(url).text

    def get_bytes(self, url: str) -> bytes:
        return self.get(url).content


class ObsidianStyleConverter(MarkdownConverter):
    def __init__(self, client: ZhihuClient, assets_dir: str, include_images: bool, image_lock: threading.Lock, **options):
        super().__init__(**options)
        self.client = client
        self.assets_dir = assets_dir
        self.include_images = include_images
        self.image_lock = image_lock

    def chomp(self, text):
        prefix = " " if text and text[0] == " " else ""
        suffix = " " if text and text[-1] == " " else ""
        text = text.strip()
        return (prefix, suffix, text)

    def convert_img(self, el, text, convert_as_inline):
        alt = el.attrs.get("alt", "") or ""
        src = el.attrs.get("src", "") or ""
        if not src:
            return ""
        if not self.include_images:
            return f"![{alt}]({src})\n\n"

        img_content_name = asset_name_from_url(src)
        img_path = os.path.join(self.assets_dir, img_content_name)
        if not os.path.exists(img_path):
            with self.image_lock:
                if not os.path.exists(img_path):
                    img_content = self.client.get_bytes(src)
                    with open(img_path, "wb") as file:
                        file.write(img_content)

        return f"![[{img_content_name}]]\n({alt})\n\n"

    def convert_a(self, el, text, convert_as_inline):
        prefix, suffix, text = self.chomp(text)
        if not text:
            return ""
        href = el.get("href")

        if el.get("aria-labelledby") and "ref" in el.get("aria-labelledby"):
            text = text.replace("[", "[^")
            return f"{prefix}{text}{suffix}"
        if (el.attrs and "data-reference-link" in el.attrs) or (
            "class" in el.attrs and ("ReferenceList-backLink" in el.attrs["class"])
        ):
            if href and len(href) > 5:
                return f"[^{href[5]}]: "
            return ""

        return super().convert_a(el, text, convert_as_inline)

    def convert_li(self, el, text, convert_as_inline):
        if el and el.find("a", {"aria-label": "back"}) is not None:
            return f"{(text or '').strip()}\n"
        return super().convert_li(el, text, convert_as_inline)


def markdownify(html: str, client: ZhihuClient, assets_dir: str, include_images: bool, image_lock: threading.Lock, **options) -> str:
    converter = ObsidianStyleConverter(
        client=client,
        assets_dir=assets_dir,
        include_images=include_images,
        image_lock=image_lock,
        **options,
    )
    return converter.convert(html)


def asset_name_from_url(url: str) -> str:
    parsed = urlparse(url)
    name = os.path.basename(parsed.path) or "image"
    if "." not in name:
        name = f"{name}.jpg"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    stem, ext = os.path.splitext(name)
    return f"{stem}_{digest}{ext}"


def html_template(data) -> str:
    return f"""
        <html>
        <head>
        </head>
        <body>
        {data}
        </body>
        </html>
        """


def clean_content_links(content_root, source_url: str) -> None:
    for element in content_root.find_all("style"):
        element.extract()

    for element in content_root.select('img[src*="data:image/svg+xml"]'):
        element.extract()

    for element in content_root.find_all("a"):
        classes = element.get("class")
        if isinstance(classes, list) and classes and classes[0] == "LinkCard":
            linkcard_name = element.get("data-text")
            element.string = linkcard_name if linkcard_name is not None else element.get("href")

        href = element.get("href")
        if isinstance(href, str) and href.startswith("mailto"):
            element.name = "p"


def extract_collection_id(collection_url: str) -> str:
    return collection_url.split("?")[0].rstrip("/").split("/")[-1]


def fetch_collection_items(client: ZhihuClient, collection_id: str, limit: Optional[int] = None) -> List[CollectionItem]:
    offset = 0
    page_size = 20
    items: List[CollectionItem] = []

    while True:
        api_url = f"https://www.zhihu.com/api/v4/collections/{collection_id}/items?offset={offset}&limit={page_size}"
        content = client.get_json(api_url)
        data = content.get("data", [])
        if not data:
            break

        for entry in data:
            content_item = entry.get("content", {})
            item_type = content_item.get("type")
            item_url = content_item.get("url")
            if not item_url:
                continue

            title = None
            if item_type == "answer":
                title = content_item.get("question", {}).get("title")
            else:
                title = content_item.get("title")

            if not title:
                print(f"跳过暂不支持的收藏内容: type={item_type}, url={item_url}")
                continue

            items.append(CollectionItem(title=title, url=item_url, content_type=item_type or "unknown"))
            if limit and len(items) >= limit:
                return items

        paging = content.get("paging", {})
        if paging.get("is_end"):
            break
        offset += page_size

    return items


def get_single_answer_content(client: ZhihuClient, answer_url: str) -> str:
    soup = BeautifulSoup(client.get_text(answer_url), "lxml")
    answer_card = soup.find("div", class_="AnswerCard")
    if answer_card is None:
        raise RuntimeError("页面中未找到 AnswerCard")

    answer_content = answer_card.find("div", class_="RichContent-inner")
    if answer_content is None:
        raise RuntimeError("页面中未找到回答正文")

    clean_content_links(answer_content, answer_url)
    return html_template(answer_content)


def get_single_post_content(client: ZhihuClient, paper_url: str) -> str:
    soup = BeautifulSoup(client.get_text(paper_url), "lxml")
    post_content = soup.find("div", class_="Post-RichText")
    if post_content is None:
        return html_template("该文章链接被404, 无法直接访问")

    clean_content_links(post_content, paper_url)
    return html_template(post_content)


def ensure_output_dirs(output_dir: str) -> Tuple[str, str]:
    assets_dir = os.path.join(output_dir, "assets")
    os.makedirs(assets_dir, exist_ok=True)
    return output_dir, assets_dir


def export_one_item(
    client: ZhihuClient,
    item: CollectionItem,
    output_dir: str,
    assets_dir: str,
    include_images: bool,
    resume: bool,
    min_delay: float,
    max_delay: float,
    image_lock: threading.Lock,
) -> ExportResult:
    filename = filter_title_str(item.title) + ".md"
    output_path = os.path.join(output_dir, filename)
    if resume and os.path.exists(output_path):
        return ExportResult(title=item.title, url=item.url, status="skipped", reason="文件已存在")

    content = get_single_post_content(client, item.url) if "zhuanlan" in item.url else get_single_answer_content(client, item.url)
    md = markdownify(
        content,
        client=client,
        assets_dir=assets_dir,
        include_images=include_images,
        image_lock=image_lock,
        heading_style="ATX",
    )
    md = f"> {item.url}\n" + md
    with open(output_path, "w", encoding="utf-8") as md_file:
        md_file.write(md)

    if max_delay > 0:
        time.sleep(random.uniform(min_delay, max_delay))

    return ExportResult(title=item.title, url=item.url, status="success")


def print_collection_preview(items: List[CollectionItem]) -> None:
    print(f"共获取 {len(items)} 篇可导出内容")
    for index, item in enumerate(items[:10], start=1):
        print(f"{index:>2}. [{item.content_type}] {item.title}")
    if len(items) > 10:
        print(f"... 其余 {len(items) - 10} 篇已省略")


def run_export(options: ExportOptions) -> int:
    client = ZhihuClient(cookies=load_cookies(), timeout=options.timeout, retries=options.retries)
    output_dir, assets_dir = ensure_output_dirs(options.output_dir)
    collection_id = extract_collection_id(options.collection_url)

    try:
        items = fetch_collection_items(client, collection_id, options.limit)
    except Exception as exc:
        print(f"读取收藏夹失败: {exc}")
        return 1

    if not items:
        print("未获取到可导出的收藏内容")
        return 1

    print_collection_preview(items)
    if options.list_only:
        return 0

    image_lock = threading.Lock()
    results: List[ExportResult] = []
    with ThreadPoolExecutor(max_workers=options.workers) as executor:
        future_to_item = {
            executor.submit(
                export_one_item,
                client,
                item,
                output_dir,
                assets_dir,
                options.include_images,
                options.resume,
                options.min_delay,
                options.max_delay,
                image_lock,
            ): item
            for item in items
        }

        for future in tqdm(as_completed(future_to_item), total=len(future_to_item), desc="导出进度"):
            item = future_to_item[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(ExportResult(title=item.title, url=item.url, status="failed", reason=str(exc)))

    success_count = sum(result.status == "success" for result in results)
    skipped_count = sum(result.status == "skipped" for result in results)
    failed = [result for result in results if result.status == "failed"]

    print(f"导出完成: 成功 {success_count}，跳过 {skipped_count}，失败 {len(failed)}")
    for result in failed[:10]:
        print(f"失败: {result.title} | {result.reason}")
    if failed:
        return 2
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    options = gather_options(args)
    return run_export(options)


if __name__ == "__main__":
    raise SystemExit(main())
