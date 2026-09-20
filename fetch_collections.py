# -*- coding: utf-8 -*-
"""抓取「我的收藏夹」列表并写回 config.json。

用法::

    python fetch_collections.py               # 抓取并写回 config.json
    python fetch_collections.py --dry-run     # 只打印结果，不写文件
    python fetch_collections.py --urls        # 改写旧版 zhihuUrls.json

脚本执行成功后会顺带把 ``config.json`` 里的 ``openCollection`` 置为 ``false``，
这样紧接着就能直接运行 ``python main.py`` 开始导出。
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from zhihu_export import config as config_mod
from zhihu_export import http as http_mod
from zhihu_export import logging_utils
from zhihu_export.collections import MINE_PAGE_URL, fetch_mine_collections, parse_collection_id

LOGS_PREFIX = "openCollection"


# ---------------------------------------------------------------------------
# 配置与日志（薄封装，保持旧函数名可用）
# ---------------------------------------------------------------------------


def get_current_os() -> str:
    """返回当前操作系统标识。"""
    return config_mod.get_current_os()


def parse_output_path(path_str, os_type=None):
    """解析输出路径（保留旧接口）。"""
    return config_mod.parse_output_path(path_str, os_type)


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """加载配置文件。"""
    return config_mod.load_config(config_path)


def load_cookies() -> Dict[str, str]:
    """读取 cookies.json。"""
    return http_mod.load_cookies()


def get_logs_dir(config_path: Optional[str] = None) -> pathlib.Path:
    """根据配置（含自定义输出目录）推断日志目录。"""
    config = config_mod.load_config(config_path)
    base_output_path = config_mod.resolve_output_path(config)
    return config_mod.get_search_paths({"_base_output_path": base_output_path})[1]


def setup_logging(config_path: Optional[str] = None) -> str:
    """初始化日志，返回日志文件路径。"""
    return logging_utils.setup_logging(get_logs_dir(config_path), prefix=LOGS_PREFIX)


def build_session(cookies: Optional[Dict[str, str]] = None):
    """创建带自动重试的会话。"""
    return http_mod.create_session(cookies=cookies or {})


# ---------------------------------------------------------------------------
# 抓取
# ---------------------------------------------------------------------------


def get_collections_from_page(page_num: int = 1, cookies=None, session=None):
    """抓取「我的收藏夹」的第 N 页（HTML 解析）。

    :return: ``(收藏夹列表, 本页是否有内容)``
    """
    from zhihu_export.collections import _parse_mine_page_html

    owns_session = session is None
    session = session or build_session(cookies)

    try:
        response = session.get(MINE_PAGE_URL.format(page=page_num), timeout=20)
        response.raise_for_status()
        collections = _parse_mine_page_html(response.text)
        return collections, bool(collections)
    except Exception as exc:  # noqa: BLE001
        logging.error("获取第%s页收藏夹失败: %s", page_num, exc)
        return [], False
    finally:
        if owns_session:
            session.close()


def get_all_collections(cookies=None, session=None, max_pages: int = 50):
    """获取全部收藏夹（优先走接口，失败自动回退 HTML 翻页）。"""
    owns_session = session is None
    session = session or build_session(cookies)
    try:
        return fetch_mine_collections(session, max_pages=max_pages)
    finally:
        if owns_session:
            session.close()


# ---------------------------------------------------------------------------
# 写回
# ---------------------------------------------------------------------------


def update_config_with_collections(
    collections: List[Dict[str, str]],
    config_path: Optional[str] = None,
) -> bool:
    """把收藏夹列表写回配置文件，并把 openCollection 复位为 false。"""
    target = pathlib.Path(config_path) if config_path else config_mod.PROJECT_ROOT / config_mod.CONFIG_FILENAME
    try:
        config = config_mod.load_config(str(target)) if target.exists() else config_mod.default_config()
        config["zhihuUrls"] = collections
        config["openCollection"] = False

        with open(target, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

        logging.info("配置文件已更新，包含 %d 个收藏夹", len(collections))
        print("配置文件已更新（%s），包含 %d 个收藏夹" % (target.name, len(collections)))
        return True
    except Exception as exc:  # noqa: BLE001
        logging.error("更新配置文件失败: %s", exc)
        print("更新配置文件失败: %s" % exc)
        return False


def save_collections_to_urls_file(
    collections: List[Dict[str, str]], filename: str = "zhihuUrls.json"
) -> bool:
    """把收藏夹列表写成旧版 zhihuUrls.json。"""
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(collections, f, ensure_ascii=False, indent=2)
        logging.info("收藏夹列表已保存到: %s", filename)
        print("收藏夹列表已保存到: %s" % filename)
        return True
    except Exception as exc:  # noqa: BLE001
        logging.error("保存文件失败: %s", exc)
        print("保存文件失败: %s" % exc)
        return False


def save_collections_log(
    collections: List[Dict[str, str]],
    log_path: Optional[str] = None,
    logs_dir: Optional[pathlib.Path] = None,
) -> str:
    """保存一份收藏夹清单快照到 logs 目录。"""
    if logs_dir is None:
        logs_dir = pathlib.Path(log_path).parent if log_path else get_logs_dir()
    logs_dir = logging_utils.ensure_dir(logs_dir)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_log_path = logs_dir / ("%s_%s.json" % (LOGS_PREFIX, timestamp))

    log_data = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_collections": len(collections),
        "collections": collections,
        "log_file": log_path,
    }
    with open(json_log_path, "w", encoding="utf-8") as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)

    logging.info("详细日志已保存到: %s", json_log_path)
    print("详细日志已保存到: %s" % json_log_path)
    return str(json_log_path)


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fetch_collections.py",
        description="抓取知乎「我的收藏夹」列表并写回配置",
    )
    parser.add_argument("--config", help="配置文件路径，默认 ./config.json")
    parser.add_argument("--urls", action="store_true", help="改为写入旧版 zhihuUrls.json")
    parser.add_argument("--max-pages", type=int, default=50, help="HTML 翻页上限，默认 50")
    parser.add_argument("--dry-run", action="store_true", help="只打印结果，不写任何文件")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    print("=" * 60)
    print("知乎收藏夹获取工具")
    print("=" * 60)

    log_path = setup_logging(args.config)
    logging.info("开始执行收藏夹获取任务")
    print("日志文件: %s" % log_path)

    cookies = load_cookies()
    if not cookies:
        print("警告: 未找到有效的 cookies，可能无法获取私密收藏夹")
        logging.warning("未找到有效的 cookies")

    print("\n开始获取收藏夹列表...")
    session = build_session(cookies)
    try:
        collections = fetch_mine_collections(session, max_pages=args.max_pages)
    except KeyboardInterrupt:
        print("\n已手动中断")
        return 130
    except Exception as exc:  # noqa: BLE001
        print("获取收藏夹过程中发生错误: %s" % exc)
        logging.error("获取收藏夹过程中发生错误: %s", exc)
        return 1
    finally:
        session.close()

    if not collections:
        print("未获取到任何收藏夹")
        logging.warning("未获取到任何收藏夹")
        print("排查建议：1) 确认 cookies.json 是登录态导出；2) 参考 README 的「常见问题」")
        return 1

    print("\n总共获取到 %d 个收藏夹:" % len(collections))
    for index, collection in enumerate(collections, 1):
        print("  %2d. %s（%s）" % (index, collection["name"], parse_collection_id(collection["url"])))
        logging.info("收藏夹 %s: %s - %s", index, collection["name"], collection["url"])

    if args.dry_run:
        print("\n--dry-run 模式：未写入任何文件")
        return 0

    print("\n正在写回配置...")
    if args.urls:
        success = save_collections_to_urls_file(collections)
    else:
        success = update_config_with_collections(collections, args.config)

    if not success:
        print("配置写入失败")
        return 1

    save_collections_log(collections, log_path)
    print("\n下一步: 运行 python main.py 开始下载收藏夹内容")
    print("如需重新获取收藏夹列表，把配置里的 openCollection 设为 true 后重新运行本脚本")
    return 0


if __name__ == "__main__":
    sys.exit(main())
