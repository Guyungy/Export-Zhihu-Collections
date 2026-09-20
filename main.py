# -*- coding: utf-8 -*-
"""Export-Zhihu-Collections 主入口：把知乎收藏夹导出为本地 Markdown。

用法::

    python main.py                      # 按 config.json 导出全部收藏夹
    python main.py --list               # 只列出收藏夹与条目数量
    python main.py --only 技术-效率工具  # 只导出指定收藏夹（可重复）
    python main.py --output ~/Zhihu     # 覆盖输出目录
    python main.py --workers 8 --delay 1.0

导入本模块不会再产生任何副作用（旧版本会在 import 时创建目录、读取 cookies）。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from bs4 import BeautifulSoup
from tqdm import tqdm

from utils import filter_title_str
from zhihu_export import config as config_mod
from zhihu_export import http as http_mod
from zhihu_export import logging_utils
from zhihu_export.collections import (
    fetch_collection_items,
    get_collection_total,
    parse_collection_id,
)
from zhihu_export.converter import (
    ImageDownloader,
    ObsidianStyleConverter,
    html_template,
    markdownify,
    prefetch_content_images,
    sanitize_content,
)
from zhihu_export.http import RateLimiter

#: 正文抓取失败时的返回值（沿用历史约定）
FETCH_FAILED = -1

ANSWER_CONTAINER_SELECTORS = (
    ("div", {"class": "AnswerCard"}),
    ("div", {"class": "QuestionAnswer-content"}),
    ("div", {"class": "RichContent"}),
    ("div", {"class": "ContentItem-expandButton"}),
)

ANSWER_FALLBACK_CSS = (
    ".RichContent-inner",
    "div.RichText",
    "div.Post-RichText",
    "div.ContentItem-content",
    ".QuestionAnswer .RichContent",
)

POST_CONTAINER_SELECTORS = (
    ("div", {"class": "Post-RichText"}),
    ("div", {"class": "RichContent"}),
    ("div", {"class": "RichContent-inner"}),
    ("div", {"class": "Post-content"}),
    ("div", {"class": "Post-RichTextContainer"}),
    ("div", {"class": "ztext"}),
    ("div", {"class": "Post-Main"}),
    ("div", {"class": "Article-RichText"}),
)

POST_FALLBACK_CSS = (
    "div.RichText",
    "div.Post-content",
    "div.ContentItem-content",
    ".Post .RichContent",
    ".Post-RichTextContainer",
    ".ztext",
    ".Post-Main .RichContent",
    "[data-zop-editor]",
    ".Article-RichText",
)

# ---------------------------------------------------------------------------
# 运行时状态（沿用全局变量，保持与旧脚本的兼容）
# ---------------------------------------------------------------------------

config: Dict[str, Any] = {}
base_output_path = None
cookies: Dict[str, str] = {}
session = None
request_limiter: RateLimiter = RateLimiter(0.0)
current_collection_name = ""
processing_log: List[Dict[str, Any]] = []
debug_log_file: Optional[str] = None


# ---------------------------------------------------------------------------
# 配置与路径（薄封装，保持旧函数名可用）
# ---------------------------------------------------------------------------


def load_config():
    """加载配置文件（兼容旧调用方式）。"""
    return config_mod.load_config()


def get_current_os() -> str:
    """返回当前操作系统标识。"""
    return config_mod.get_current_os()


def parse_output_path(path_str, os_type=None):
    """把配置里的输出路径解析为绝对路径。"""
    return config_mod.parse_output_path(path_str, os_type)


def load_cookies():
    """读取 cookies.json。"""
    return http_mod.load_cookies()


def get_output_path(collection_name: str) -> str:
    """返回某个收藏夹的输出目录；未配置自定义目录时使用 ``downloads/<收藏夹名>``。"""
    base, _, _ = config_mod.get_search_paths({"_base_output_path": base_output_path})
    return str(base / filter_title_str(collection_name))


def get_logs_path() -> str:
    """返回日志目录。"""
    _, logs_dir, _ = config_mod.get_search_paths({"_base_output_path": base_output_path})
    return str(logs_dir)


def get_debug_path() -> str:
    """返回调试文件目录。"""
    _, _, debug_dir = config_mod.get_search_paths({"_base_output_path": base_output_path})
    return str(debug_dir)


def get_download_workers() -> int:
    """正文下载并发数（命令行参数优先于配置文件）。"""
    return config_mod.get_download_workers(config)


def get_image_workers() -> int:
    """单篇正文内的图片并发数。"""
    return config_mod.get_image_workers(config)


def reconfigure_logging(prefix: str = "debug", console_level: int = logging.INFO) -> str:
    """把日志切到最终确定的日志目录。"""
    global debug_log_file
    debug_log_file = logging_utils.setup_logging(
        get_logs_path(), prefix=prefix, console_level=console_level
    )
    return debug_log_file


# 兼容旧名字：早期版本分为「先初始化再重配」两步，现在一步到位
setup_debug_logging = reconfigure_logging
flush_logs = logging_utils.flush_logs


def build_session(cookie_dict: Optional[Dict[str, str]] = None):
    """创建带自动重试的会话。"""
    return http_mod.create_session(cookies=cookie_dict if cookie_dict is not None else cookies)


# ---------------------------------------------------------------------------
# 页面抓取
# ---------------------------------------------------------------------------


def fetch_page(url: str):
    """抓取页面，返回 ``(response, soup)``；请求会走全局限流。"""
    if request_limiter:
        request_limiter.wait()
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return response, BeautifulSoup(response.text, "lxml")


def smart_content_detection(soup, url):
    """标准选择器全部失效时的兜底内容检测。

    依次尝试：文本量最大的容器 → ``article``/``main`` → 多段落容器。
    """
    logging.debug("开始智能内容检测: %s", url)

    all_divs = soup.find_all("div")
    text_length_threshold = 200

    candidates = []
    for div in all_divs:
        text_content = div.get_text(strip=True)
        if len(text_content) > text_length_threshold:
            link_count = len(div.find_all("a"))
            candidates.append(
                {
                    "element": div,
                    "text_length": len(text_content),
                    "text_to_link_ratio": len(text_content) / max(link_count, 1),
                    "classes": div.get("class", []),
                }
            )

    candidates.sort(key=lambda x: x["text_length"], reverse=True)
    if candidates and candidates[0]["text_length"] > 500:
        best = candidates[0]
        logging.debug("智能检测命中容器，长度: %s, classes: %s", best["text_length"], best["classes"])
        return best["element"]

    for container in soup.find_all(["article", "main"]):
        if len(container.get_text(strip=True)) > text_length_threshold:
            logging.debug("智能检测命中文章容器: %s", container.name)
            return container

    for div in all_divs:
        paragraphs = div.find_all("p")
        if len(paragraphs) >= 3:
            total = sum(len(p.get_text(strip=True)) for p in paragraphs)
            if total > text_length_threshold:
                logging.debug("智能检测命中多段落容器，段落数: %s", len(paragraphs))
                return div

    logging.debug("智能内容检测未找到合适的内容")
    return None


def analyze_page_error(soup, response, url: str) -> str:
    """分析页面为什么解析不出正文（404 / 需要登录 / 被删除 / 改版）。"""
    page_text = response.text.lower()

    if "404" in page_text or "not found" in page_text or "页面不存在" in response.text:
        return "该文章链接被404, 无法直接访问"
    if "请先登录" in response.text or "登录" in response.text or "login" in page_text:
        return "该文章需要登录访问，请检查 cookies 配置"
    if "403" in page_text or "forbidden" in page_text or "访问被拒绝" in response.text:
        return "该文章访问被拒绝，可能需要特殊权限"
    if "已删除" in response.text or "内容不存在" in response.text or "deleted" in page_text:
        return "该文章内容已被删除或不存在"
    if response.url != url:
        return "页面被重定向到: %s, 可能是登录页或错误页" % response.url
    if not any(indicator in page_text for indicator in ("知乎", "zhihu", "www.zhihu.com")):
        return "页面结构异常，可能不是正常的知乎页面"
    return "页面结构可能发生变化，无法解析文章内容"


def _save_debug_html(response, prefix: str, url: str) -> None:
    """把无法解析的页面落盘，方便排查。"""
    debug_dir = get_debug_path()
    os.makedirs(debug_dir, exist_ok=True)
    debug_file = os.path.join(debug_dir, "debug_%s_%s.html" % (prefix, url.split("/")[-1]))
    with open(debug_file, "w", encoding="utf-8") as f:
        f.write(response.text)
    logging.debug("页面 HTML 已保存到: %s", debug_file)


def _find_container(soup, primary_selectors, fallback_css, url):
    """按「指定选择器 → CSS 兜底 → 智能检测」的顺序找正文容器。"""
    for tag, attrs in primary_selectors:
        elements = soup.find_all(tag, attrs)
        if elements:
            logging.debug("找到 %d 个 %s %s 元素", len(elements), tag, attrs)
            for element in elements:
                inner = element.find("div", class_="RichContent-inner")
                if inner:
                    logging.debug("命中 RichContent-inner")
                    return inner
            logging.debug("直接使用 %s 容器", attrs.get("class"))
            return elements[0]

    for selector in fallback_css:
        node = soup.select_one(selector)
        if node:
            logging.debug("使用备用选择器命中内容: %s", selector)
            return node

    node = smart_content_detection(soup, url)
    if node is not None:
        logging.debug("使用智能内容检测命中内容")
    return node


def get_single_answer_content(answer_url: str):
    """抓取回答（或想法）正文，返回 HTML 字符串；失败返回 ``FETCH_FAILED``。"""
    logging.debug("开始获取回答内容: %s", answer_url)

    try:
        response, soup = fetch_page(answer_url)
        answer_content = _find_container(
            soup, ANSWER_CONTAINER_SELECTORS, ANSWER_FALLBACK_CSS, answer_url
        )

        if answer_content is None:
            reason = analyze_page_error(soup, response, answer_url)
            logging.error("未找到回答内容容器: %s - %s", answer_url, reason)
            _save_debug_html(response, "answer", answer_url)
            return FETCH_FAILED

        sanitize_content(answer_content)
    except Exception as exc:  # noqa: BLE001 - 单篇失败不影响整体
        logging.error("获取回答内容时发生错误: %s", exc)
        logging.error("URL: %s", answer_url)
        logging.debug("Traceback: %s", traceback.format_exc())
        return FETCH_FAILED

    return html_template(answer_content)


def get_single_post_content(paper_url: str):
    """抓取专栏文章正文，返回 HTML 字符串；失败返回 ``FETCH_FAILED``。"""
    logging.debug("开始获取专栏文章内容: %s", paper_url)

    try:
        response, soup = fetch_page(paper_url)
        post_content = _find_container(
            soup, POST_CONTAINER_SELECTORS, POST_FALLBACK_CSS, paper_url
        )

        if post_content is None:
            reason = analyze_page_error(soup, response, paper_url)
            logging.error("未找到专栏内容容器: %s - %s", paper_url, reason)
            _save_debug_html(response, "post", paper_url)
            return FETCH_FAILED

        sanitize_content(post_content)
    except Exception as exc:  # noqa: BLE001
        logging.error("获取专栏文章内容时发生错误: %s", exc)
        logging.error("URL: %s", paper_url)
        logging.debug("Traceback: %s", traceback.format_exc())
        return FETCH_FAILED

    return html_template(post_content)


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------


def is_article_already_downloaded(file_path: str, target_url: str) -> bool:
    """文件已存在、非空、且首行引用块里的 URL 与目标一致，视为已下载。"""
    if not os.path.exists(file_path):
        return False
    if os.path.getsize(file_path) == 0:  # 上次中断留下的空文件，重下
        return False

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            first_line = f.readline().strip()
        return first_line.startswith("> ") and target_url in first_line
    except OSError:
        return False


def get_unique_filename(base_dir: str, title: str, url: str) -> str:
    """返回不冲突的文件路径；同名文章会带上 URL 尾部 ID。"""
    base_filename = filter_title_str(title)
    file_path = os.path.join(base_dir, base_filename + ".md")

    if not os.path.exists(file_path):
        return file_path
    if is_article_already_downloaded(file_path, url):
        return file_path

    url_id = url.split("/")[-1]
    return os.path.join(base_dir, "%s_%s.md" % (base_filename, url_id))


def build_reserved_file_path(base_dir: str, title: str, url: str, reserved_paths: set) -> str:
    """在并发下载前为任务占位，避免同标题不同文章写进同一个文件。"""
    file_path = get_unique_filename(base_dir, title, url)
    candidate_path = file_path

    while candidate_path in reserved_paths:
        base_name, ext = os.path.splitext(file_path)
        duplicate_suffix = url.split("/")[-1]
        candidate_path = "%s_%s%s" % (base_name, duplicate_suffix, ext)
        if candidate_path in reserved_paths:
            candidate_path = "%s_%s_%s%s" % (base_name, duplicate_suffix, len(reserved_paths), ext)

    reserved_paths.add(candidate_path)
    return candidate_path


def save_processing_log() -> str:
    """把本次处理的逐篇结果写成 JSON 日志。"""
    logs_dir = get_logs_path()
    os.makedirs(logs_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(logs_dir, "%s.json" % timestamp)

    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(processing_log, f, ensure_ascii=False, indent=2)

    print("处理日志已保存到: %s" % log_path)
    return log_path


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------


def download_single_article(task: Dict[str, Any]) -> Dict[str, Any]:
    """下载单篇文章（线程池任务）。

    :param task: ``{title, url, type, file_path, assets_dir, image_workers}``
    """
    title = task["title"]
    url = task["url"]
    file_path = task["file_path"]

    article_log: Dict[str, Any] = {
        "name": title,
        "url": url,
        "type": task.get("type", ""),
        "status": "",
    }

    if is_article_already_downloaded(file_path, url):
        article_log["status"] = "文章已存在,跳过下载"
        return article_log

    try:
        logging.info("开始下载文章: %s", title)

        if "zhuanlan" in url:
            content = get_single_post_content(url)
        else:
            content = get_single_answer_content(url)

        if content == FETCH_FAILED:
            article_log["status"] = "文章下载失败, 原因:获取内容失败"
            logging.warning("获取内容失败: %s", url)
            return article_log

        downloader = task.get("image_downloader")
        if downloader is not None:
            # 先把图片并发拉下来，转换阶段直接命中缓存
            soup = BeautifulSoup(content, "lxml")
            prefetch_content_images(soup, downloader)
            content = str(soup)

        converter = ObsidianStyleConverter(image_downloader=downloader)
        md = converter.convert(content)
        md = "> %s\n" % url + md

        with open(file_path, "w", encoding="utf-8") as md_file:
            md_file.write(md)

        article_log["images"] = converter.stats["images"]
        article_log["image_failures"] = converter.stats["image_failures"]
        article_log["status"] = "正常下载"
        logging.info("文章下载成功: %s", title)
        return article_log
    except Exception as exc:  # noqa: BLE001 - 单篇失败记录后继续
        article_log["status"] = "文章下载失败, 原因:%s" % exc
        logging.error("下载文章时发生错误: %s", title)
        logging.error("错误详情: %s", exc)
        logging.error("URL: %s", url)
        logging.debug("Traceback: %s", traceback.format_exc())
        return article_log


def process_single_collection(
    collection_name: str,
    collection_url: str,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """处理单个收藏夹：取列表 → 过滤已下载 → 并发下载正文。"""
    global current_collection_name
    current_collection_name = collection_name

    logging.info("开始处理收藏夹: %s", collection_name)
    logging.info("收藏夹URL: %s", collection_url)

    collection_log: Dict[str, Any] = {
        "name": collection_name,
        "url": collection_url,
        "list": [],
    }

    collection_id = parse_collection_id(collection_url)
    logging.info("解析得到收藏夹ID: %s", collection_id)

    try:
        items = fetch_collection_items(
            session,
            collection_id,
            limiter=request_limiter,
            max_items=1 if dry_run else None,
        )
    except Exception as exc:  # noqa: BLE001
        logging.error("处理收藏夹 '%s' 时发生错误: %s", collection_name, exc)
        logging.debug("Traceback: %s", traceback.format_exc())
        collection_log["error"] = str(exc)
        processing_log.append(collection_log)
        return collection_log

    if not items:
        logging.warning("收藏夹 '%s' 没有获取到任何文章", collection_name)
        print("收藏夹 '%s' 没有获取到任何可导出文章" % collection_name)
        processing_log.append(collection_log)
        return collection_log

    print("收藏夹 '%s' 共获取 %d 篇可导出回答或专栏" % (collection_name, len(items)))
    if dry_run:
        for item in items:
            print("  - [%s] %s" % (item.type_label, item.title))
        collection_log["dry_run"] = True
        processing_log.append(collection_log)
        return collection_log

    download_dir = get_output_path(collection_name)
    os.makedirs(download_dir, exist_ok=True)
    assets_dir = os.path.join(download_dir, "assets")

    image_downloader = None
    if not config.get("skipImages"):
        image_downloader = ImageDownloader(session, assets_dir, max_workers=get_image_workers())

    reserved_paths: set = set()
    download_tasks: List[Dict[str, Any]] = []
    skipped_logs: List[Dict[str, Any]] = []

    for item in items:
        file_path = build_reserved_file_path(download_dir, item.title, item.url, reserved_paths)
        if is_article_already_downloaded(file_path, item.url):
            skipped_logs.append(
                {"name": item.title, "url": item.url, "type": item.type, "status": "文章已存在,跳过下载"}
            )
            continue
        download_tasks.append(
            {
                "title": item.title,
                "url": item.url,
                "type": item.type,
                "file_path": file_path,
                "image_downloader": image_downloader,
            }
        )

    collection_log["list"].extend(skipped_logs)

    worker_count = get_download_workers()
    if download_tasks:
        print("使用 %d 个线程下载正文（已跳过 %d 篇）" % (worker_count, len(skipped_logs)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(download_single_article, task) for task in download_tasks]
            for future in tqdm(as_completed(futures), total=len(futures), desc="处理 %s" % collection_name):
                collection_log["list"].append(future.result())
    else:
        print("收藏夹 '%s' 全部已下载，无需重复处理" % collection_name)

    if image_downloader is not None:
        collection_log["images"] = dict(image_downloader.stats)

    processing_log.append(collection_log)
    print("收藏夹 '%s' 下载完毕" % collection_name)
    return collection_log


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------


def summarize() -> Dict[str, int]:
    """统计本次处理的成功 / 跳过 / 失败数量。"""
    stats = {"collections": len(processing_log), "total": 0, "downloaded": 0, "skipped": 0, "failed": 0}
    for collection in processing_log:
        for entry in collection.get("list", []):
            stats["total"] += 1
            status = entry.get("status", "")
            if status.startswith("正常下载"):
                stats["downloaded"] += 1
            elif "跳过" in status:
                stats["skipped"] += 1
            else:
                stats["failed"] += 1
    return stats


def print_summary() -> None:
    """打印处理结果汇总表。"""
    stats = summarize()
    base, _, _ = config_mod.get_search_paths({"_base_output_path": base_output_path})
    print("\n" + "=" * 52)
    print("导出完成")
    print("-" * 52)
    print("收藏夹  : %d 个" % stats["collections"])
    print("文章    : 共 %d 篇 | 新下载 %d | 跳过 %d | 失败 %d"
          % (stats["total"], stats["downloaded"], stats["skipped"], stats["failed"]))
    print("输出目录: %s" % base)
    if debug_log_file:
        print("日志    : %s" % debug_log_file)
    print("=" * 52)


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="把知乎收藏夹导出为本地 Markdown（Obsidian 友好）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", help="配置文件路径，默认 ./config.json")
    parser.add_argument("--output", help="覆盖配置里的 outputPath")
    parser.add_argument("--only", action="append", default=[], metavar="NAME_OR_ID",
                        help="只导出指定收藏夹（按名称或 ID，可重复传入）")
    parser.add_argument("--workers", type=int, help="正文下载并发数（1-16）")
    parser.add_argument("--delay", type=float, help="每次请求之间的最小间隔秒数")
    parser.add_argument("--skip-images", action="store_true", help="只导出文字，不下载正文图片")
    parser.add_argument("--list", action="store_true", help="只列出收藏夹与条目，不下载")
    parser.add_argument("--dry-run", action="store_true", help="每个收藏夹只试抓 1 条，用于验证配置")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试级日志")
    return parser


def _select_collections(collections: Sequence[Dict[str, Any]], only: Sequence[str]):
    """按 --only 过滤收藏夹（名称或 ID 命中即可）。"""
    if not only:
        return list(collections)

    wanted = {str(v).strip().lower() for v in only}
    selected = []
    for collection in collections:
        name = str(collection.get("name", "")).strip().lower()
        cid = parse_collection_id(collection.get("url", "")).lower()
        if name in wanted or cid in wanted:
            selected.append(collection)
        elif any(w and (w in name or w in cid) for w in wanted):
            selected.append(collection)
    return selected


def main(argv: Optional[Sequence[str]] = None) -> int:
    """程序入口，返回进程退出码。"""
    global config, base_output_path, cookies, session, request_limiter, processing_log

    args = build_arg_parser().parse_args(argv)

    # 1) 配置
    config = config_mod.load_config(args.config)
    if args.workers:
        config["downloadWorkers"] = args.workers
    if args.delay is not None:
        config["requestDelay"] = args.delay
    if args.skip_images:
        config["skipImages"] = True

    # 2) 输出路径
    base_output_path = config_mod.resolve_output_path(config, override=args.output)
    if base_output_path:
        print("使用自定义输出路径: %s" % base_output_path)
        config["_base_output_path"] = base_output_path
    else:
        print("使用默认输出路径: %s/" % config_mod.DOWNLOAD_DIRNAME)

    # 3) 日志（在输出路径确定之后再初始化，日志会落在同一个根目录下）
    reconfigure_logging()
    logging.debug("启动参数: %s", vars(args))

    # 4) cookies 与会话
    cookies = load_cookies()
    session = build_session(cookies)
    request_limiter = RateLimiter(config_mod.get_request_delay(config))

    # 5) 模式判断
    if config.get("openCollection"):
        print("检测到 openCollection 模式已启用")
        print("请先运行 python fetch_collections.py 获取收藏夹列表")
        print("脚本会自动把 openCollection 置为 false，然后重新运行 python main.py")
        return 1

    collections = config.get("zhihuUrls") or []
    if not collections:
        print("没有找到要处理的收藏夹配置")
        print("提示：先运行 python fetch_collections.py 自动获取收藏夹列表")
        return 1

    collections = _select_collections(collections, args.only)
    if not collections:
        print("--only 没有匹配到任何收藏夹，请用 --list 查看可用名称")
        return 1

    print("共找到 %d 个收藏夹待处理" % len(collections))

    if args.list:
        print("-" * 52)
        for index, collection in enumerate(collections, 1):
            name = collection.get("name", "未命名收藏夹")
            cid = parse_collection_id(collection.get("url", ""))
            total = 0
            try:
                total = get_collection_total(session, cid, request_limiter)
            except Exception as exc:  # noqa: BLE001 - 列表模式不该因为单个失败而中断
                logging.warning("获取收藏夹 %s 条目数失败: %s", cid, exc)
            print("%2d. %-24s %s (%s 条)" % (index, name, cid, total))
        print("-" * 52)
        return 0

    processing_log = []
    try:
        for collection in collections:
            name = collection.get("name", "未命名收藏夹")
            url = collection.get("url", "")
            if not url:
                print("收藏夹 '%s' 缺少 URL，跳过" % name)
                continue
            print("\n开始处理收藏夹: %s" % name)
            process_single_collection(name, url, dry_run=args.dry_run)
    except KeyboardInterrupt:
        print("\n已手动中断，正在保存已完成的进度...")
        logging.warning("用户中断了导出流程")
    finally:
        if processing_log:
            save_processing_log()
            print_summary()

    return 0


if __name__ == "__main__":
    sys.exit(main())
