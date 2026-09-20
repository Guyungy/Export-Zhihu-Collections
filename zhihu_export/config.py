# -*- coding: utf-8 -*-
"""配置加载、校验与跨平台输出路径解析。

这个模块是配置中枢：

* 统一 ``config.json`` 与旧版 ``zhihuUrls.json`` 的加载逻辑
* 把 ``outputPath`` 解析成当前系统可用的路径（支持按系统分别配置）
* 对「路径与系统不匹配」「并发数越界」这类误配置给出明确告警
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import platform
import re
from typing import Any, Dict, Optional, Union

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

CONFIG_FILENAME = "config.json"
EXAMPLE_CONFIG_FILENAME = "config.example.json"
LEGACY_URLS_FILENAME = "zhihuUrls.json"
COOKIES_FILENAME = "cookies.json"
DOWNLOAD_DIRNAME = "downloads"
LOGS_DIRNAME = "logs"
DEBUG_DIRNAME = "debug"

DEFAULT_DOWNLOAD_WORKERS = 6
MAX_DOWNLOAD_WORKERS = 16
DEFAULT_IMAGE_WORKERS = 4
MAX_IMAGE_WORKERS = 16
DEFAULT_REQUEST_DELAY = 0.4  # 每个正文请求之间的最小间隔（秒）
MAX_REQUEST_DELAY = 10.0
DEFAULT_PAGE_TIMEOUT = 30.0  # 普通页面超时（秒）
DEFAULT_LONG_PAGE_TIMEOUT = 120.0  # 大专栏文章的读超时（秒）
MAX_PAGE_TIMEOUT = 600.0
DEFAULT_FETCH_RETRIES = 3  # 正文抓取的应用层重试次数
MAX_FETCH_RETRIES = 10

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")

# 把各种写法归一到本工具内部使用的系统名
_OS_ALIASES: Dict[str, str] = {
    "windows": "windows",
    "win": "windows",
    "win32": "windows",
    "nt": "windows",
    "macos": "macos",
    "mac": "macos",
    "osx": "macos",
    "darwin": "macos",
    "linux": "linux",
    "freebsd": "freebsd",
    "openbsd": "openbsd",
    "netbsd": "netbsd",
    "solaris": "solaris",
    "aix": "aix",
    "cygwin": "cygwin",
    "msys": "cygwin",
    "msys2": "cygwin",
}


def get_current_os() -> str:
    """返回当前操作系统标识（windows / macos / linux / unknown）。"""
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "macos"
    if system == "linux":
        return "linux"
    return system or "unknown"


def normalize_os(os_type: Optional[str]) -> Optional[str]:
    """把配置里的 ``os`` 字段归一化，无法识别时返回 ``None``。

    ``""`` / ``"auto"`` 表示交给程序自动判断。
    """
    if not os_type:
        return None
    key = str(os_type).strip().lower()
    if key in {"", "auto", "automatic", "default"}:
        return None
    return _OS_ALIASES.get(key, key)


def is_windows_path(path_str: str) -> bool:
    """判断是否是带盘符的 Windows 路径（如 ``D:/foo``、``D:\\foo``）。"""
    return bool(path_str) and bool(_WINDOWS_DRIVE_RE.match(str(path_str)))


def default_config() -> Dict[str, Any]:
    """返回一份最小可用配置。"""
    return {
        "zhihuUrls": [],
        "outputPath": "",
        "os": "",
        "openCollection": False,
        "downloadWorkers": DEFAULT_DOWNLOAD_WORKERS,
        "requestDelay": DEFAULT_REQUEST_DELAY,
    }


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """加载配置。

    :param config_path: 指定的配置文件路径；为空时依次尝试
        ``config.json`` → 旧版 ``zhihuUrls.json``。
    :return: 配置字典（任何情况下都返回 dict，缺失时是默认配置）
    """
    path = pathlib.Path(config_path) if config_path else PROJECT_ROOT / CONFIG_FILENAME

    try:
        with open(path, "r", encoding="utf-8") as f:
            config = json.load(f)
        if not isinstance(config, dict):
            raise ValueError("配置文件顶层必须是 JSON 对象")
        return config
    except FileNotFoundError:
        if config_path:
            logging.error("未找到指定的配置文件: %s", config_path)
            print("未找到指定的配置文件: %s" % config_path)
            print("可以复制仓库里的 %s 作为起点" % EXAMPLE_CONFIG_FILENAME)
            return default_config()
    except (json.JSONDecodeError, ValueError) as exc:
        logging.error("配置文件格式错误 %s: %s", path, exc)
        print("配置文件格式错误: %s（%s）" % (path, exc))
        return default_config()

    # 向后兼容：只有 zhihuUrls.json 的老用户
    print("未找到 %s，尝试读取旧版 %s" % (CONFIG_FILENAME, LEGACY_URLS_FILENAME))
    legacy = PROJECT_ROOT / LEGACY_URLS_FILENAME
    try:
        with open(legacy, "r", encoding="utf-8") as f:
            urls = json.load(f)
        config = default_config()
        config["zhihuUrls"] = urls if isinstance(urls, list) else []
        return config
    except FileNotFoundError:
        print("未找到配置文件，请先创建 %s 并配置收藏夹信息" % CONFIG_FILENAME)
        print("  cp %s %s" % (EXAMPLE_CONFIG_FILENAME, CONFIG_FILENAME))
        print("（%s 里有可直接改用的字段与默认值）" % EXAMPLE_CONFIG_FILENAME)
        return default_config()
    except json.JSONDecodeError as exc:
        print("旧版 %s 格式错误: %s" % (LEGACY_URLS_FILENAME, exc))
        return default_config()


def parse_output_path(path_str: str, os_type: Optional[str] = None) -> Optional[pathlib.Path]:
    """把配置里的路径字符串解析为当前系统下的绝对路径。

    :param path_str: 用户填写的路径（支持 ``~`` 与 ``/cygdrive/c/...`` 写法）
    :param os_type: 目标系统；为空则自动判断
    :return: 解析后的路径，无法解析时返回 ``None``
    """
    if not path_str:
        return None

    target_os = normalize_os(os_type) or get_current_os()

    try:
        if target_os == "windows":
            path_str = str(path_str).replace("/", "\\")
            return pathlib.Path(path_str).expanduser().resolve()

        if target_os == "cygwin":
            if path_str.startswith("/cygdrive/"):
                drive_path = path_str[10:]
                if len(drive_path) >= 2 and drive_path[1] == "/":
                    path_str = drive_path[0].upper() + ":" + drive_path[1:].replace("/", "\\")
            elif path_str.startswith("/") and len(path_str) >= 3 and path_str[2] == "/":
                path_str = path_str[1].upper() + ":" + path_str[2:].replace("/", "\\")
            return pathlib.Path(path_str).expanduser().resolve()

        if target_os in {"linux", "freebsd", "openbsd", "netbsd", "solaris", "aix", "macos"}:
            return pathlib.Path(os.path.expanduser(str(path_str))).resolve()

        logging.warning("未知操作系统类型: %s，按当前系统通用规则解析路径", target_os)
        return pathlib.Path(os.path.expanduser(str(path_str))).resolve()
    except Exception as exc:  # noqa: BLE001 - 路径解析失败不应该中断程序
        logging.error("路径解析失败: %s, 错误: %s", path_str, exc)
        return None


def resolve_output_path(
    config: Dict[str, Any],
    force_os: Optional[str] = None,
    override: Optional[str] = None,
) -> Optional[pathlib.Path]:
    """根据配置与命令行覆盖项，决定最终的输出根目录。

    支持两种 ``outputPath`` 写法::

        "outputPath": "~/Documents/ZhihuExports"

        "outputPath": {
            "windows": "D:/Documents/ZhihuExports",
            "macos": "~/Documents/ZhihuExports"
        }

    :param override: 命令行 ``--output`` 传入的路径，优先级最高
    :return: 输出根目录；``None`` 表示使用默认的 ``downloads/``
    """
    current_os = get_current_os()

    if override:
        path = parse_output_path(override, current_os)
        if path:
            return path
        print("输出路径解析失败，回退到默认目录 %s/" % DOWNLOAD_DIRNAME)
        return None

    raw = config.get("outputPath")
    if not raw:
        return None

    configured_os = normalize_os(force_os) or normalize_os(config.get("os"))

    # 写法一：按系统分别配置（只认当前系统，以及显式的 default 兜底）
    if isinstance(raw, dict):
        for key in (current_os, "default"):
            candidate = raw.get(key)
            if candidate:
                if key != current_os:
                    logging.info("outputPath 未配置 %s 路径，使用 default 路径", current_os)
                return parse_output_path(candidate, current_os)
        print("outputPath 中没有适用于当前系统（%s）的路径，回退到默认目录 %s/" % (current_os, DOWNLOAD_DIRNAME))
        return None

    if not isinstance(raw, str):
        print("outputPath 类型不支持（应为字符串或对象），回退到默认目录 %s/" % DOWNLOAD_DIRNAME)
        return None

    # 写法二：单个字符串，但需要防止「Windows 路径在 macOS/Linux 上被当成相对目录」
    if is_windows_path(raw) and current_os != "windows":
        print("检测到 Windows 路径但当前系统是 %s：%s" % (current_os, raw))
        print("已忽略该配置，回退到默认目录 %s/（可用 --output 指定实际路径）" % DOWNLOAD_DIRNAME)
        logging.warning("outputPath 与当前系统不匹配，已回退默认目录: %s", raw)
        return None

    if configured_os and configured_os != current_os:
        logging.warning("配置中的 os=%s 与当前系统 %s 不一致，按当前系统规则解析路径", configured_os, current_os)
        print("提示：配置里的 os=%s 与当前系统 %s 不一致，已按当前系统规则解析路径" % (configured_os, current_os))

    return parse_output_path(raw, current_os)


def _bounded_int(raw: Any, default: int, minimum: int, maximum: int) -> int:
    """把配置项转成夹在 [minimum, maximum] 区间内的整数。"""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def _bounded_float(raw: Any, default: float, minimum: float, maximum: float) -> float:
    """把配置项转成夹在 [minimum, maximum] 区间内的浮点数。"""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def get_download_workers(config: Dict[str, Any]) -> int:
    """获取正文下载并发数（1 ~ 16）。"""
    return _bounded_int(
        config.get("downloadWorkers", DEFAULT_DOWNLOAD_WORKERS),
        DEFAULT_DOWNLOAD_WORKERS,
        1,
        MAX_DOWNLOAD_WORKERS,
    )


def get_image_workers(config: Dict[str, Any]) -> int:
    """获取单篇文章内部图片下载并发数（1 ~ 16）。"""
    return _bounded_int(
        config.get("imageWorkers", DEFAULT_IMAGE_WORKERS),
        DEFAULT_IMAGE_WORKERS,
        1,
        MAX_IMAGE_WORKERS,
    )


def get_request_delay(config: Dict[str, Any]) -> float:
    """获取每个请求之间的最小间隔（秒）。"""
    return _bounded_float(
        config.get("requestDelay", DEFAULT_REQUEST_DELAY),
        DEFAULT_REQUEST_DELAY,
        0.0,
        MAX_REQUEST_DELAY,
    )


def get_page_timeout(config: Dict[str, Any]) -> float:
    """获取普通页面请求超时（秒）。"""
    return _bounded_float(
        config.get("pageTimeout", DEFAULT_PAGE_TIMEOUT),
        DEFAULT_PAGE_TIMEOUT,
        1.0,
        MAX_PAGE_TIMEOUT,
    )


def get_long_page_timeout(config: Dict[str, Any]) -> float:
    """获取大专栏文章的读超时（秒）。

    专栏文章正文动辄上万字，配套的图片也多，用普通超时很容易中途断流。
    """
    return _bounded_float(
        config.get("longPageTimeout", DEFAULT_LONG_PAGE_TIMEOUT),
        DEFAULT_LONG_PAGE_TIMEOUT,
        1.0,
        MAX_PAGE_TIMEOUT,
    )


def get_fetch_retries(config: Dict[str, Any]) -> int:
    """获取正文抓取的应用层重试次数。"""
    return _bounded_int(
        config.get("fetchRetries", DEFAULT_FETCH_RETRIES),
        DEFAULT_FETCH_RETRIES,
        0,
        MAX_FETCH_RETRIES,
    )


def is_api_fallback_enabled(config: Dict[str, Any]) -> bool:
    """是否允许在页面抓取失败时回退到 OpenAPI（默认开启）。"""
    value = config.get("apiFallback", True)
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off")
    return bool(value)


def get_search_paths(config: Dict[str, Any]) -> tuple:
    """返回 (输出根目录, 日志目录, 调试目录)。"""
    base_output_path: Optional[Union[pathlib.Path, None]] = config.get("_base_output_path")
    if base_output_path:
        base = pathlib.Path(base_output_path)
        return base, base / LOGS_DIRNAME, base / DEBUG_DIRNAME

    base = PROJECT_ROOT / DOWNLOAD_DIRNAME
    return base, base / LOGS_DIRNAME, base / DEBUG_DIRNAME
