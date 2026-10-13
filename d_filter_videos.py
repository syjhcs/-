import asyncio
import json
import os
import signal
from urllib.parse import quote
from invisible_playwright.async_api import InvisiblePlaywright

FILE_PATH = 'tv_name.json'

from z_browser_config import get_profile_dir, get_headless


def _term_handler(signum, frame):
    raise KeyboardInterrupt(f"收到信号 {signum}")


try:
    signal.signal(signal.SIGTERM, _term_handler)
except (ValueError, AttributeError, OSError):
    pass


def _rollback(title):
    try:
        with open(FILE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return
    for item in data:
        if isinstance(item, dict) and item.get('title') == title \
                and item.get('release_date') == '正在处理中':
            item['release_date'] = '未知'
            break
    else:
        return
    with open(FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        f.flush()
        os.fsync(f.fileno())
    print(f"[回滚] 《{title}》release_date 已改回 '未知'")


async def _close(pw):
    try:
        await pw.__aexit__(None, None, None)
    except Exception:
        pass


# ================= ★ 新增：关闭抖音弹窗 =================
async def _close_douyin_popup(page):
    """关闭抖音的'是否续播手机在看的直播？'等弹窗。"""
    print("[弹窗] 检查是否有弹窗...")

    # ---------- 1) 找按钮点击 ----------
    for text in ['取消', '暂不', '关闭', '知道了']:
        try:
            btn = page.locator(f'text={text}')
            n = await btn.count()
            for i in range(n):
                try:
                    b = btn.nth(i)
                    if await b.is_visible():
                        await b.click(timeout=2000)
                        print(f'[弹窗] 点击"{text}"关闭弹窗')
                        await asyncio.sleep(0.8)
                        return True
                except Exception:
                    continue
        except Exception:
            continue

    # ---------- 2) 按 ESC ----------
    try:
        await page.keyboard.press('Escape')
        await asyncio.sleep(0.5)
    except Exception:
        pass

    # ---------- 3) JS 移除"是否续播"的 pace-island ----------
    try:
        removed = await page.evaluate("""
            (() => {
                let cnt = 0;
                const islands = document.querySelectorAll('pace-island');
                for (const el of islands) {
                    const t = el.textContent || '';
                    if (t.includes('是否续播') || t.includes('手机在看的直播')) {
                        el.remove();
                        cnt++;
                    }
                }
                return cnt;
            })()
        """)
        if removed:
            print(f'[弹窗] JS 移除 {removed} 个弹窗')
            await asyncio.sleep(0.5)
            return True
    except Exception:
        pass

    print("[弹窗] 未发现弹窗（或已处理）")
    return False


# ==========================================================


async def filter_videos():
    # ---------- 读 tv_name.json，找待处理 ----------
    try:
        with open(FILE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f'错误：未找到 {FILE_PATH}')
        return None, None, None, None
    if not data:
        print(f'{FILE_PATH} 没有数据！')
        return None, None, None, None

    target = next((i for i in data if i.get('release_date') == '正在处理中'), None)
    if target is None:
        target = next((i for i in data if i.get('release_date') == '未知'), None)
    if target is None:
        print('没有待处理电视剧！')
        return None, None, None, None

    title = target['title']
    print(f'待处理电视剧: {title}')
    target['release_date'] = '正在处理中'
    with open(FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        f.flush()
        os.fsync(f.fileno())

    # ---------- 手动生命周期 ----------
    pw = InvisiblePlaywright(seed=42, profile_dir=get_profile_dir('douyin'), headless=get_headless())
    try:
        browser = await pw.__aenter__()
    except BaseException as e:
        print(f"浏览器启动失败：{e}")
        _rollback(title)
        raise

    try:
        page = await browser.new_page()
        await page.goto(
            f"https://www.douyin.com/search/{quote(title)}",
            wait_until='domcontentloaded',
        )
        await asyncio.sleep(3)

        # ★ 关键修复：先关掉"是否续播手机在看的直播？"弹窗
        await _close_douyin_popup(page)
        await asyncio.sleep(1)

        # 多列
        mc = page.locator('text=多列')
        if await mc.count() > 0:
            try:
                await mc.first.click(timeout=3000)
            except Exception as e:
                print(f'多列点击失败：{e}')
        else:
            print('未找到多列按钮！')
        await asyncio.sleep(1)

        # 筛选按钮
        fb = None
        fl = page.locator('text=筛选')
        for i in range(await fl.count()):
            if await fl.nth(i).is_visible():
                fb = fl.nth(i)
                break

        if fb is None:
            print('未找到筛选按钮！')
            await _close(pw)
            return None, None, None, None

        # 最多点赞
        await fb.hover()
        await asyncio.sleep(1)
        ml = page.locator('text=最多点赞')
        if await ml.count() > 0:
            await ml.first.click(timeout=3000)
        else:
            print('未找到最多点赞按钮！')
        await asyncio.sleep(1)

        # 一周内
        await fb.hover()
        await asyncio.sleep(1)
        ow = page.locator('text=一周内')
        if await ow.count() > 0:
            await ow.first.click(timeout=3000)
            await asyncio.sleep(3)
        else:
            print('未找到一周内按钮！')

        # ================= ★ 新增：卡死检测与自动重试逻辑（带状态重置） =================
        await asyncio.sleep(2)

        max_check_attempts = 5
        for check_attempt in range(max_check_attempts):
            print(f"[检查] 正在等待视频瀑布流加载... (第 {check_attempt + 1}/{max_check_attempts} 次)")
            try:
                # 尝试等待瀑布流元素出现，如果5秒内没出现，就进入重试逻辑
                await page.wait_for_selector('div[id^="waterfall_item_"]', timeout=5000)
                print("✅ 视频瀑布流已成功加载！")
                break  # 加载成功，跳出循环，准备交给 e_crawl_video_id.py
            except Exception:
                print("⚠️ 视频未加载（页面卡住），执行状态重置与重试逻辑...")

                # 方案 A：模拟你的手动操作，先点“关注的人”，再点“不限”，最后点回“一周内”
                try:
                    # 1. 点击“关注的人”强制重置前端状态
                    focus_btn = page.locator('text=关注的人').first
                    if await focus_btn.count() > 0:
                        await focus_btn.click(timeout=3000)
                        print("  🔄 已点击'关注的人'重置状态，等待1秒...")
                        await asyncio.sleep(1)

                    # 2. 点击“不限”（搜索范围下的）
                    # 注意页面上可能有多个“不限”，这里优先找“关注的人”前面的兄弟节点（通常“不限”在“关注的人”左边）
                    unlimited_btn = page.locator('text=关注的人').locator('xpath=preceding-sibling::*[1]')
                    if await unlimited_btn.count() > 0:
                        await unlimited_btn.click(timeout=3000)
                        print("  🔄 已点击'不限'重置状态，等待1秒...")
                        await asyncio.sleep(1)
                    else:
                        # 兜底：直接找可见的“不限”按钮中的第一个
                        fallback_btn = page.locator('text=不限').first
                        if await fallback_btn.count() > 0:
                            await fallback_btn.click(timeout=3000)
                            await asyncio.sleep(1)

                    # 3. 重新点击“一周内”
                    await ow.first.click(timeout=3000)
                    await asyncio.sleep(3)
                    print("  🔄 已重新点击'一周内'。")

                except Exception as e:
                    print(f"  状态重置失败：{e}，准备执行刷新页面...")

                    # 方案 B：如果重置无效，直接刷新页面，然后重新应用筛选
                    try:
                        await page.reload(wait_until='domcontentloaded')
                        await asyncio.sleep(3)

                        # 刷新后需要重新做一遍筛选操作
                        mc_retry = page.locator('text=多列')
                        if await mc_retry.count() > 0:
                            await mc_retry.first.click(timeout=3000)
                            await asyncio.sleep(1)

                        await fb.hover()
                        await asyncio.sleep(1)
                        ml_retry = page.locator('text=最多点赞')
                        if await ml_retry.count() > 0:
                            await ml_retry.first.click(timeout=3000)
                            await asyncio.sleep(1)

                        await fb.hover()
                        await asyncio.sleep(1)
                        await ow.first.click(timeout=3000)
                        await asyncio.sleep(3)

                        print("  🔄 已尝试刷新页面并重新筛选。")
                    except Exception as ex:
                        print(f"  刷新并重新筛选失败：{ex}")
        else:
            print("❌ 多次重试后依然无法加载视频，跳过当前剧名。")
            await _close(pw)
            _rollback(title)
            return None, None, None, None
        # ==========================================================

        print('筛选操作已完成')
        return pw, browser, page, title

    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n检测到中断，回滚 release_date...")
        await _close(pw)
        _rollback(title)
        raise

    except BaseException as e:
        print(f"筛选过程中出现异常：{e}")
        await _close(pw)
        _rollback(title)
        raise