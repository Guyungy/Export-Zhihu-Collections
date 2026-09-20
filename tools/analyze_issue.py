# -*- coding: utf-8 -*-
"""诊断工具：检查 cookies 与知乎页面结构是否仍然可用。

用法::

    python tools/analyze_issue.py

当「我的收藏夹」抓不到内容时，用这个脚本快速判断问题出在哪一层：
cookies 是否有效 → 接口是否可用 → 页面结构是否改版 → 正文 API 兜底通道是否可用。
"""

from __future__ import annotations

import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zhihu_export import config as config_mod
from zhihu_export import http as http_mod
from zhihu_export.collections import ME_API, MINE_PAGE_URL, fetch_mine_collections

CHECK_COOKIES = ("z_c0", "d_c0", "SESSIONID")


def check_cookies() -> bool:
    """检查 cookies 是否存在、是否包含登录票据。"""
    print("=== 1. 检查 cookies ===")
    cookie_path = config_mod.PROJECT_ROOT / config_mod.COOKIES_FILENAME
    cookies = http_mod.load_cookies()

    if not cookies:
        print("✗ 未读取到 cookies：%s 不存在或格式不对" % cookie_path)
        print("  影响：只能导出公开收藏夹")
        return False

    print("✓ 读取到 %d 个 cookie" % len(cookies))
    ok = True
    for name in CHECK_COOKIES:
        if name in cookies:
            print("✓ 存在关键 cookie: %s" % name)
        else:
            print("✗ 缺少关键 cookie: %s" % name)
            ok = False
    if not ok:
        print("  建议：重新从浏览器导出「登录态」cookies，私密收藏夹依赖 z_c0")
    return ok


def check_api(session) -> bool:
    """检查登录接口是否可用。"""
    print("\n=== 2. 检查登录接口 ===")
    try:
        response = session.get(ME_API, timeout=20)
        print("HTTP %s" % response.status_code)
        if response.status_code != 200:
            print("✗ 接口不可用，可能是 cookies 失效或触发风控")
            return False
        payload = response.json()
        print("✓ 当前登录账号: %s（url_token=%s）"
              % (payload.get("name", "未知"), payload.get("url_token", "-")))
        return True
    except Exception as exc:  # noqa: BLE001
        print("✗ 请求失败: %s" % exc)
        return False


def check_mine_page(session) -> None:
    """检查「我的收藏夹」页面结构。"""
    print("\n=== 3. 检查收藏夹页面结构 ===")
    try:
        response = session.get(MINE_PAGE_URL.format(page=1), timeout=20)
        print("HTTP %s，页面长度 %d" % (response.status_code, len(response.text)))
        print("SelfCollectionItem 出现次数: %d" % response.text.count("SelfCollectionItem"))
        print("/collection/ 链接出现次数: %d" % response.text.count("/collection/"))
    except Exception as exc:  # noqa: BLE001
        print("✗ 请求失败: %s" % exc)


def check_collections(session) -> None:
    """真实跑一次收藏夹抓取，看能拿到多少。"""
    print("\n=== 4. 试跑收藏夹抓取 ===")
    try:
        collections = fetch_mine_collections(session, max_pages=3)
        print("✓ 获取到 %d 个收藏夹" % len(collections))
        for index, collection in enumerate(collections[:5], 1):
            print("  %d. %s" % (index, collection["name"]))
    except Exception as exc:  # noqa: BLE001
        print("✗ 抓取失败: %s" % exc)


def check_content_api(session) -> bool:
    """检查正文 API 兜底通道是否可用。

    页面路线被 403 / 改版挡住时，导出会退到这几个接口取正文。
    这里逐个探一遍，把机器码翻译成人话。
    """
    print("\n=== 5. 检查正文 API 兜底通道 ===")
    from zhihu_export import api as api_mod

    # 用一组稳定的公开内容做探针
    probes = (
        ("回答", "https://www.zhihu.com/question/20482274/answer/15637067"),
        ("专栏", "https://zhuanlan.zhihu.com/p/386395767"),
    )

    healthy = 0
    for label, url in probes:
        target = api_mod.parse_content_target(url)
        api_url = api_mod.build_api_url(*target) if target else None
        if not api_url:
            print("✗ %s: URL 无法解析出内容 ID" % label)
            continue

        try:
            response = session.get(api_url, headers=http_mod.build_api_headers(), timeout=20)
        except Exception as exc:  # noqa: BLE001
            print("✗ %s: 请求失败 %s" % (label, exc))
            continue

        content_type = (response.headers.get("Content-Type") or "").lower()
        if "json" in content_type:
            payload = response.json()
            if api_mod.extract_content_html(payload, target[0]):
                print("✓ %s: 可取正文" % label)
                healthy += 1
            else:
                print("✗ %s: HTTP %s | %s"
                      % (label, response.status_code, api_mod.describe_api_error(payload) or "响应体为空"))
        else:
            print("✗ %s: HTTP %s 返回非 JSON（可能被风控拦截）" % (label, response.status_code))

    if healthy:
        print("  说明：页面抓取失败时，这些接口能顶上来")
    else:
        print("  说明：兜底通道也不可用 —— 通常还是 cookies 失效，先重新导出 cookies")
    return healthy > 0


def main() -> int:
    print("知乎收藏夹抓取诊断")
    print("=" * 50)
    cookies = http_mod.load_cookies()
    cookie_ok = check_cookies()
    session = http_mod.create_session(cookies=cookies)
    api_ok = check_api(session)
    check_mine_page(session)
    if api_ok or cookie_ok:
        check_collections(session)
    check_content_api(session)

    print("\n=== 结论 ===")
    if api_ok:
        print("登录态正常，抓取应可用。若仍失败，请查看 downloads/logs/ 下的日志。")
        return 0
    print("登录态异常：请先更新 cookies.json，再重试 fetch_collections.py")
    return 1


if __name__ == "__main__":
    sys.exit(main())
