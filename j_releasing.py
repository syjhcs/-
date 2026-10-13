# j_releasing.py  (Camoufox 版
# windows系统，只支持有头，linux支持无头，，兼容旧 pw 参数)
import asyncio
import os
import time
import json
import re
import random
import pyperclip
import win32com.client
from camoufox.async_api import AsyncCamoufox
from z_browser_config import get_headless

# ================= 用户目录配置（Camoufox / Firefox 会在这里创建 profile） =================
CHROME_USER_DIR = r'C:\Users\syjhc\AppData\Local\Google\Chrome\baijiahao'
CHROME_EXE = r'C:\Program Files\Google\Chrome\Application\chrome.exe'


def _resolve_chrome_exe():
    """按常见位置找一个存在的 chrome.exe，找不到返回 None（改用 channel='chrome'）。"""
    candidates = [
        CHROME_EXE,
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        r'C:\Users\syjhc\AppData\Local\Google\Chrome\Application\chrome.exe',
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


async def launch_chrome_context():
    """用 Camoufox（补丁版 Firefox）+ 指定用户数据目录启动持久化上下文。"""
    os.makedirs(CHROME_USER_DIR, exist_ok=True)

    camoufox = AsyncCamoufox(
        persistent_context=True,
        user_data_dir=CHROME_USER_DIR,
        headless=get_headless(),  # 从 z_browser_config 读全局开关
        humanize=True,  # 关闭鼠标拟人化（避免鼠标乱飘）；想更强反检测可改 True
        viewport={"width": 1500, "height": 900},
    )
    context = await camoufox.__aenter__()
    return camoufox, context


# ================= 通用 =================

def get_processing_title():
    try:
        with open('tv_name.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if not isinstance(data, list):
        return None
    for item in data:
        if isinstance(item, dict) and item.get('release_date') == '正在处理中':
            return item.get('title')
    return None


def get_last_target_from_video_ids(json_path='video_ids.json'):
    if not os.path.exists(json_path):
        print(f"未找到文件：{json_path}")
        return None, 0
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"读取 {json_path} 失败：{e}")
        return None, 0

    last_id, last_split_count = None, 0
    for item in data:
        if item.get('status') != '已抓取':
            continue
        try:
            sc = int(item.get('split_count', 0))
        except (TypeError, ValueError):
            sc = 0
        if sc > 0:
            last_id = item.get('id')
            last_split_count = sc
    return last_id, last_split_count
# ================= 暂停状态 =================

_publish_paused = False
_stop_after_current = False
_pause_reason = None
PAUSE_STATE_FILE = 'pause_state.json'

_state_loaded = False


def _save_persisted_pause():
    try:
        with open(PAUSE_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                'paused': _publish_paused,
                'reason': _pause_reason,
                'pause_date': time.strftime('%Y-%m-%d'),
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[警告] 保存暂停状态失败：{e}")


def _clear_persisted_pause():
    try:
        if os.path.exists(PAUSE_STATE_FILE):
            os.remove(PAUSE_STATE_FILE)
            print(f"[状态] 已清除持久化暂停文件 {PAUSE_STATE_FILE}")
    except Exception as e:
        print(f"[警告] 删除暂停状态文件失败：{e}")


def _load_persisted_pause():
    global _publish_paused, _pause_reason
    if not os.path.exists(PAUSE_STATE_FILE):
        return
    try:
        with open(PAUSE_STATE_FILE, 'r', encoding='utf-8') as f:
            state = json.load(f)
    except Exception:
        return
    if not state.get('paused'):
        _clear_persisted_pause()
        return
    pause_date = state.get('pause_date', '')
    today = time.strftime('%Y-%m-%d')
    if pause_date == today:
        _publish_paused = True
        _pause_reason = state.get('reason', 'PLATFORM_ZERO')
        print(f"[状态] 从文件恢复暂停（{pause_date} 平台额度已耗尽），继续挂起。")
    else:
        print(f"[状态] 跨天（暂停于 {pause_date}，今天 {today}），自动解除。")
        _clear_persisted_pause()


def _ensure_state_loaded():
    global _state_loaded
    if not _state_loaded:
        _state_loaded = True
        _load_persisted_pause()


def is_publish_paused():
    _ensure_state_loaded()
    return _publish_paused


def get_pause_reason():
    _ensure_state_loaded()
    return _pause_reason


def reset_publish_paused():
    global _publish_paused, _pause_reason, _stop_after_current, _state_loaded
    _publish_paused = False
    _pause_reason = None
    _stop_after_current = False
    _state_loaded = True
    _clear_persisted_pause()
    print("[状态] 发布暂停已解除。")


def _reset_on_platform_limit():
    global _publish_paused, _pause_reason, _state_loaded
    print("[状态] 平台额度已满，进入暂停状态。")
    _pause_reason = 'PLATFORM_ZERO'
    _publish_paused = True
    _state_loaded = True
    _save_persisted_pause()


# ================= 额度检测 =================

async def check_publish_quota_via_hover(page, publish_btn, max_wait=3):
    global _stop_after_current
    if publish_btn is None:
        return True

    print("[悬停检测] 正在滚动到发布按钮并触发悬停...")
    try:
        await publish_btn.scroll_into_view_if_needed()
        await asyncio.sleep(random.uniform(0.3, 0.8))
        await publish_btn.hover()
    except Exception as e:
        print(f"[悬停检测] hover 失败: {e}，准备 JS 兜底")

    await asyncio.sleep(random.uniform(0.8, 1.8))

    limit_text = ""
    popover = page.locator('div.cheetah-popover-inner-content').first
    if await popover.count() > 0:
        try:
            await popover.wait_for(state='visible', timeout=1000)
            limit_text = await popover.inner_text()
        except Exception:
            pass

    if not limit_text or '今日还可发布' not in limit_text:
        print("[悬停检测] DOM 未拿到，改用 JS 读 textContent...")
        try:
            await publish_btn.evaluate("""
                (el) => {
                    el.dispatchEvent(new MouseEvent('mouseover', {
                        bubbles: true, cancelable: true
                    }));
                }
            """)
            await asyncio.sleep(random.uniform(0.8, 1.5))
            limit_text = await page.evaluate("""
                (() => {
                    const els = document.querySelectorAll('.cheetah-popover-inner-content');
                    for (let el of els) {
                        if (el.textContent && el.textContent.includes('今日还可发布')) {
                            return el.textContent;
                        }
                    }
                    return '';
                })()
            """)
        except Exception as e:
            print(f"[悬停检测] JS 读取失败: {e}")

    if limit_text:
        print(f"[悬停检测] 提示文本: {limit_text.strip()}")
        match = re.search(r'今日还可发布(\d+)篇内容', limit_text)
        if match:
            remaining = int(match.group(1))
            if remaining == 0:
                print("[悬停检测] 额度 0，拦截！")
                _reset_on_platform_limit()
                return False
            elif remaining == 1:
                print("[悬停检测] 仅剩 1 篇，本次发完后暂停。")
                _stop_after_current = True
                return True
            else:
                print(f"点击按钮等待发布中")
                return True
        else:
            print(f"[悬停检测] 未匹配数字：{limit_text}，默认放行。")
    else:
        print("[悬停检测] 未抓到提示，放行。")
    return True


async def _has_today_limit(page, max_wait=5):
    end = time.time() + max_wait
    while time.time() < end:
        try:
            limit_ele = page.locator('text=今日还可发布0篇内容').first
            if await limit_ele.count() > 0:
                print("[检测] 发现“今日还可发布0篇内容”，额度满。")
                _reset_on_platform_limit()
                return True

            popover = page.locator('div.cheetah-popover-inner-content').first
            if await popover.count() > 0:
                try:
                    text = await popover.inner_text()
                    if '今日还可发布0篇内容' in text:
                        _reset_on_platform_limit()
                        return True
                except Exception:
                    pass

            btn = page.locator('[data-testid="publish-btn"]').first
            if await btn.count() > 0:
                disabled = await btn.get_attribute('disabled')
                if disabled is not None:
                    print("[检测] 发布按钮 disabled，额度满。")
                    _reset_on_platform_limit()
                    return True
                else:
                    print("[检测] 发布按钮可点，还有额度。")
                    return False
        except Exception:
            pass
        await asyncio.sleep(random.uniform(0.8, 1.2))
    print(f"[检测] 等待 {max_wait}s 超时，放行。")
    return False


# ================= Word 提取 =================

def get_title_and_copy_body(filepath):
    if not os.path.exists(filepath):
        print(f"未找到文件：{filepath}")
        return None
    abs_path = os.path.abspath(filepath)
    print("调用 Word 提取...")
    try:
        word_app = win32com.client.Dispatch("Word.Application")
        word_app.Visible = False
        doc = word_app.Documents.Open(abs_path)
        first_line = ""
        start_pos = 0
        for para in doc.Paragraphs:
            if para.Range.Text.strip():
                first_line = para.Range.Text.strip()
                start_pos = para.Range.End
                break
        body_copied = False
        if start_pos < doc.Content.End:
            rng = doc.Range(Start=start_pos, End=doc.Content.End)
            rng.Copy()
            body_copied = True
        doc.Close(False)
        word_app.Quit()
        if body_copied:
            print("正文已复制到剪贴板。")
        return first_line
    except Exception as e:
        print(f"Word 失败：{e}")
        return None


# ================= JSON 操作 =================

def get_published_count(json_path, video_id):
    if not os.path.exists(json_path):
        return 0
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return 0
    for item in data:
        if str(item.get('id')) == str(video_id):
            try:
                return int(item.get('published_count', 0))
            except (TypeError, ValueError):
                return 0
    return 0


def update_published_count(json_path, video_id, count):
    if not os.path.exists(json_path):
        return
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return
    for item in data:
        if str(item.get('id')) == str(video_id):
            item['published_count'] = int(count)
            break
    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        print(f"已更新 {json_path}：id={video_id} published_count={count}")
    except Exception:
        pass


def set_published_note(json_path, video_id, note):
    if not os.path.exists(json_path):
        return
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return
    for item in data:
        if str(item.get('id')) == str(video_id):
            item['note'] = note
            break
    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception:
        pass


def mark_published_in_json(json_path, video_id):
    if not os.path.exists(json_path):
        return
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return
    for item in data:
        if str(item.get('id')) == str(video_id):
            item['published'] = '已发布'
            item.pop('status', None)
            break
    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        print(f"已标记 id={video_id} published=已发布")
    except Exception:
        pass


def write_task_success(json_path='task.json'):
    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump({'task': 'success'}, f, ensure_ascii=False, indent=4)
        print(f"已生成 {json_path}")
    except Exception as e:
        print(f"生成 {json_path} 失败：{e}")


# ================= 单次发布（★ 兼容旧 pw 参数）=================

async def run_once(docx_filename, pw=None, context=None):
    """返回 True=成功 / False=失败 / 'PAUSED'

    参数：
      - context 不为 None → 复用外部传入的 BrowserContext（新接口，推荐）
      - pw      不为 None → 兼容旧调用，忽略它（不再使用反检测库），自建 Camoufox
      - 两者都 None       → 自建 Camoufox，用完自己关
    """
    global _publish_paused, _stop_after_current, _pause_reason

    _ensure_state_loaded()

    if _publish_paused:
        print("[跳过] 处于暂停状态，返回 'PAUSED'。")
        return 'PAUSED'

    if pw is not None and context is None:
        print("[兼容] 检测到旧的 pw 参数，已忽略（改用 Camoufox 启动）。")

    first_line = get_title_and_copy_body(docx_filename)
    if not first_line:
        print(f"无法提取标题：{docx_filename}")
        return False

    own_context = context is None
    camoufox_obj = None

    if own_context:
        print("正在启动浏览器（Camoufox）...")
        try:
            camoufox_obj, context = await launch_chrome_context()
        except Exception as e:
            print(f"❌ 启动 Camoufox 失败：{e}")
            return False
    else:
        print("[浏览器] 复用外部传入的 context ...")

    try:
        if context.pages:
            page = context.pages[0]
            print(f"[浏览器] 复用已打开页面：{page.url}")
        else:
            page = await context.new_page()

        await page.goto('https://baijiahao.baidu.com/', wait_until='commit')
        try:
            await page.wait_for_load_state('domcontentloaded', timeout=10000)
        except Exception:
            pass

        await asyncio.sleep(random.uniform(1.5, 2.5))
        print("检查活动弹窗...")
        try:
            close_btn = page.locator('.activity-close').first
            if await close_btn.count() > 0 and await close_btn.is_visible():
                await close_btn.click()
                print("已关闭活动弹窗。")
                await asyncio.sleep(random.uniform(0.8, 1.5))
        except Exception as e:
            print(f"弹窗处理异常（可忽略）：{e}")

        publish_btn = page.locator('text=发布作品').first
        if await publish_btn.count() == 0:
            print("=" * 60)
            print("⚠️ 未找到「发布作品」按钮，可能未登录百家号。")
            try:
                print(f"当前页面：{await page.title()}")
                print(f"当前 URL：{page.url}")
            except Exception:
                pass
            print("请在浏览器窗口里扫码登录百家号。")
            print("登录成功后程序会自动检测并继续（最长等待 10 分钟）。")
            print("=" * 60)

            max_wait_rounds = 200
            logged_in = False
            for i in range(max_wait_rounds):
                await asyncio.sleep(3)
                try:
                    publish_btn = page.locator('text=发布作品').first
                    if await publish_btn.count() > 0:
                        print(f"✅ 检测到「发布作品」按钮，登录成功！")
                        logged_in = True
                        break
                except Exception:
                    pass
                if (i + 1) % 10 == 0:
                    waited = (i + 1) * 3
                    print(f"  ⏳ 等待登录中...（已等 {waited} 秒 / 最长 {max_wait_rounds * 3} 秒）")

            if not logged_in:
                print("❌ 等待登录超时（10 分钟），放弃本次发布。")
                return False

        print("[登录后] 等页面稳定 3 秒...")
        await asyncio.sleep(3)

        try:
            await page.wait_for_load_state('domcontentloaded', timeout=10000)
        except Exception:
            pass

        publish_btn = None
        all_btns = page.locator('text=发布作品')
        n = await all_btns.count()
        print(f"[定位] 找到 {n} 个「发布作品」元素")
        for i in range(n):
            b = all_btns.nth(i)
            try:
                if await b.is_visible():
                    publish_btn = b
                    print(f"[定位] 第 {i + 1} 个可见，选它")
                    break
            except Exception:
                continue

        if publish_btn is None:
            print("❌ 没有可见的「发布作品」，放弃")
            return False

        try:
            pages_before = list(context.pages)
        except Exception:
            pages_before = []

        click_ok = False
        try:
            await publish_btn.click(timeout=5000, force=True)
            click_ok = True
            print("强制点击成功")
        except Exception as e:
            print(f"  force 点击失败：{e}")

        if not click_ok:
            try:
                result = await page.evaluate("""
                    (() => {
                        const all = document.querySelectorAll('a, button, div, span');
                        const candidates = [];
                        for (const el of all) {
                            const text = (el.textContent || '').trim();
                            if (text === '发布作品' && el.offsetParent !== null) {
                                const rect = el.getBoundingClientRect();
                                if (rect.width > 0 && rect.height > 0) {
                                    candidates.push({el, area: rect.width * rect.height});
                                }
                            }
                        }
                        if (candidates.length === 0) return 'no_visible';
                        candidates.sort((a, b) => a.area - b.area);
                        candidates[0].el.click();
                        return 'clicked';
                    })()
                """)
                if result == 'clicked':
                    click_ok = True
                    print("✅ JS 点击「发布作品」成功")
                else:
                    print(f"  JS 点击返回：{result}")
            except Exception as e:
                print(f"  JS 点击失败：{e}")

        if not click_ok:
            print("❌ 所有方式都点不动「发布作品」，放弃本次发布。")
            return False
        await asyncio.sleep(random.uniform(2.5, 4.5))

        try:
            pages_after = list(context.pages)
            new_pages = [p for p in pages_after if p not in pages_before]
            if new_pages:
                page = new_pages[-1]
                print(f"[标签页] 检测到新标签页：{page.url}")
                try:
                    await page.wait_for_load_state('domcontentloaded', timeout=10000)
                except Exception:
                    pass
            else:
                print("[标签页] 当前页跳转")
                try:
                    await page.wait_for_load_state('domcontentloaded', timeout=10000)
                except Exception:
                    pass
        except Exception as e:
            print(f"[标签页] 判断新旧页出错（忽略）：{e}")

        print("已进入编辑器，标题:", await page.title())
        await asyncio.sleep(random.uniform(2.5, 4.5))

        if await _has_today_limit(page, max_wait=5):
            print("进入编辑器后额度已满，暂停。")
            write_task_success()
            return 'PAUSED'

        title_container = page.locator('[data-testid="news-title-input"]').first
        if await title_container.count() > 0:
            editor_area = title_container.locator('[contenteditable="true"]').first
            if await editor_area.count() > 0:
                await editor_area.click()
                await asyncio.sleep(random.uniform(0.3, 0.8))
                await page.keyboard.press('Control+A')
                await page.keyboard.press('Delete')
                await asyncio.sleep(random.uniform(0.3, 0.8))
                pyperclip.copy(first_line)
                await page.keyboard.press('Control+V')
                print(f"标题已填入：{first_line}")
                await asyncio.sleep(random.uniform(0.8, 1.5))

        print("重新复制正文...")
        get_title_and_copy_body(docx_filename)

        print("定位正文编辑区...")
        try:
            await page.wait_for_selector('#ueditor_0', timeout=10000)
        except Exception:
            print("未找到 #ueditor_0")
            return False

        frame = None
        for f in page.frames:
            if f.name == 'ueditor_0' or '#ueditor_0' in (f.url or ''):
                frame = f
                break

        try:
            if frame is not None:
                await frame.evaluate('document.body.focus(); window.getSelection().removeAllRanges();')
            else:
                holder = page.locator('#edui1_iframeholder').first
                if await holder.count() > 0:
                    await holder.click()
        except Exception:
            pass

        await asyncio.sleep(random.uniform(0.8, 1.5))

        try:
            await page.keyboard.press('Control+A')
            await page.keyboard.press('Delete')
            await asyncio.sleep(random.uniform(0.3, 0.8))
            await page.keyboard.press('Control+V')
            print("正文（图文）粘贴成功！")
        except Exception as e:
            print(f"粘贴出错：{e}")
            return False

        await asyncio.sleep(random.uniform(2.5, 4.5))

        # ================= 新增：自动化插入商品（原著书籍） =================
        tv_name = get_processing_title()
        if tv_name:
            print(f"正文粘贴完成，准备先插入商品：{tv_name} ...")
            try:
                # 1. 点击“插入”
                insert_btn_locator = page.get_by_text("插入", exact=True).first
                await insert_btn_locator.scroll_into_view_if_needed()
                await asyncio.sleep(0.5)
                await insert_btn_locator.click(force=True)
                await asyncio.sleep(1.5)

                # 兜底：菜单没出来就 hover 一次
                if await page.locator('text="商品"').count() == 0 or \
                        not await page.locator('text="商品"').first.is_visible():
                    print("  -> 菜单未展开，补一次 hover「插入」")
                    await insert_btn_locator.hover(force=True)
                    await asyncio.sleep(random.uniform(0.8, 1.2))

                # 2. 物理鼠标移到“商品”再点
                print("  -> 点击“商品”")
                goods_menu = page.locator('text="商品"').first
                try:
                    await goods_menu.wait_for(state='visible', timeout=3000)
                except Exception:
                    print("  -> ⚠️ 未等到「商品」可见，仍尝试点击")

                try:
                    box = await goods_menu.bounding_box()
                    if box:
                        gx = box["x"] + box["width"] / 2
                        gy = box["y"] + box["height"] / 2
                        await page.mouse.move(gx - 20, gy, steps=random.randint(5, 10))
                        await asyncio.sleep(random.uniform(0.1, 0.25))
                        await page.mouse.move(gx, gy, steps=random.randint(3, 6))
                        await asyncio.sleep(random.uniform(0.1, 0.25))
                        await page.mouse.click(gx, gy)
                    else:
                        await goods_menu.click(timeout=8000, force=True)
                except Exception as _e:
                    print(f"  -> 物理点击「商品」失败：{_e}，改 force click")
                    await goods_menu.click(timeout=8000, force=True)
                await asyncio.sleep(2)

                # ★★★ 3. 商品弹窗在独立 iframe（cecom.baidu.com）里，先切进去 ★★★
                print("  -> 选择平台：拼多多")

                goods_frame = None
                for _fr in page.frames:
                    _u = _fr.url or ''
                    if 'cecom.baidu.com' in _u and 'choosegoods' in _u:
                        goods_frame = _fr
                        break

                if goods_frame is None:
                    # 兜底：找含 .ant-select-selector 的 iframe
                    for _fr in page.frames:
                        if _fr == page.main_frame:
                            continue
                        try:
                            if await _fr.locator('.ant-select-selector').count() > 0:
                                goods_frame = _fr
                                break
                        except Exception:
                            continue

                if goods_frame is None:
                    print("  -> ❌ 找不到商品 iframe，跳过商品插入")
                    raise RuntimeError("找不到商品 iframe")

                print(f"  -> [iframe] 已定位商品框架：{goods_frame.url[:80]}")

                # 3.1 在 iframe 里找“全部平台”下拉框（打开菜单）
                _sel_list = goods_frame.locator('.ant-select-selector')
                _total = await _sel_list.count()
                print(f"  -> [诊断] iframe 里 .ant-select-selector 数量：{_total}")

                platform_selector = None
                for _i in range(_total):
                    _t = _sel_list.nth(_i)
                    try:
                        _vis = await _t.is_visible()
                        _txt = ""
                        try:
                            _txt = (await _t.inner_text()).strip()
                        except Exception:
                            pass
                        print(f"     [{_i}] visible={_vis}, text={_txt[:40]!r}")
                        if _vis and '全部平台' in _txt and platform_selector is None:
                            platform_selector = _t
                    except Exception as _e:
                        print(f"     [{_i}] 检查出错: {_e}")
                        continue

                if platform_selector is not None:
                    try:
                        await platform_selector.click(timeout=5000)
                        print("  -> ✅ 点中“全部平台”下拉框（普通点击）")
                    except Exception as _e:
                        print(f"  -> ⚠️ 普通点击失败：{_e}，改用 force 点击")
                        await platform_selector.click(force=True, timeout=5000)
                        print("  -> ✅ 点中“全部平台”下拉框（force 点击）")
                else:
                    print("  -> ❌ iframe 里没找到可见的“全部平台”触发器，跳过商品插入")
                    raise RuntimeError("找不到全部平台下拉框")

                await asyncio.sleep(random.uniform(0.8, 1.4))

                # 4. 选择“拼多多”（在 iframe 里）
                _vis_dropdown = goods_frame.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden)')
                print(f"  -> [诊断] iframe 未隐藏 dropdown 数量：{await _vis_dropdown.count()}")

                _opts = goods_frame.locator(
                    '.ant-select-dropdown:not(.ant-select-dropdown-hidden) '
                    '.ant-select-item-option'
                )
                _n_opts = await _opts.count()
                print(f"  -> [诊断] iframe 可见 dropdown 选项数：{_n_opts}")
                for _i in range(min(_n_opts, 10)):
                    try:
                        _label = await _opts.nth(_i).get_attribute('label')
                        print(f"     [选项 {_i}] label={_label!r}")
                    except Exception:
                        pass

                pdd_option = goods_frame.locator(
                    '.ant-select-dropdown:not(.ant-select-dropdown-hidden) '
                    '.ant-select-item-option[label="拼多多"]'
                ).first

                if await pdd_option.count() > 0:
                    try:
                        box = await pdd_option.bounding_box()
                        if box:
                            x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
                            y = box["y"] + box["height"] * random.uniform(0.4, 0.6)
                            await page.mouse.move(x - 20, y, steps=random.randint(6, 12))
                            await asyncio.sleep(random.uniform(0.1, 0.3))
                            await page.mouse.move(x, y, steps=random.randint(3, 6))
                            await asyncio.sleep(random.uniform(0.15, 0.4))
                            await page.mouse.click(x, y)
                        else:
                            await pdd_option.click(force=True)
                        print("  -> ✅ 已选择拼多多")
                    except Exception as _e:
                        print(f"  -> ⚠️ 选拼多多出错：{_e}")
                        await pdd_option.click(force=True)
                else:
                    print("  -> ❌ 未在可见 dropdown 里找到 label='拼多多' 的选项")
                    raise RuntimeError("找不到拼多多选项")

                await asyncio.sleep(random.uniform(0.8, 1.4))

                # 5. 点击“全部分类”（同 iframe）
                print("  -> 选择分类：图书教育")
                category_selector = None
                for _i in range(await _sel_list.count()):
                    _t = _sel_list.nth(_i)
                    try:
                        if await _t.is_visible() and '全部分类' in (await _t.inner_text()):
                            category_selector = _t
                            break
                    except Exception:
                        continue
                if category_selector is not None:
                    try:
                        await category_selector.click(timeout=5000)
                    except Exception:
                        await category_selector.click(force=True)
                else:
                    print("  -> ⚠️ 未找到“全部分类”，尝试 text 兜底")
                    await goods_frame.locator('text="全部分类"').first.click(force=True)
                await asyncio.sleep(random.uniform(0.8, 1.4))

                # 6. 选择“图书教育”（同 iframe）
                edu_option = goods_frame.locator(
                    '.ant-select-dropdown:not(.ant-select-dropdown-hidden) '
                    '.ant-select-item-option[label="图书教育"]'
                ).first
                if await edu_option.count() > 0:
                    try:
                        await edu_option.click(timeout=5000)
                    except Exception:
                        await edu_option.click(force=True)
                else:
                    edu_option = goods_frame.locator('.ant-select-item-option[label="图书教育"]').first
                    if await edu_option.count() > 0:
                        await edu_option.click(force=True)
                    else:
                        await goods_frame.locator('text="图书教育"').first.click(force=True)
                await asyncio.sleep(random.uniform(0.8, 1.4))

                # 7. 搜索框输入剧名（同 iframe）
                print(f"  -> 搜索框输入：{tv_name}")
                # ★ 关键：用 :visible 精确命中可见的那个 input（隐藏的那个同名 class 是假的）
                # 先让分类下拉的搜索框失焦
                await goods_frame.evaluate("""
                    () => {
                        if (document.activeElement) document.activeElement.blur();
                    }
                """)
                await asyncio.sleep(0.3)

                search_input = goods_frame.locator('input.choosegoods-search-input:visible').first
                _cnt = await search_input.count()
                print(f"  -> [定位] 可见搜索框数量：{_cnt}")

                await search_input.click()
                await asyncio.sleep(0.3)
                await search_input.fill("")  # 清空
                await asyncio.sleep(0.2)
                await search_input.type(tv_name, delay=random.randint(80, 130))
                await asyncio.sleep(0.5)

                _val = await search_input.input_value()
                print(f"  -> [验证] 搜索框 value = {_val!r}")
                await asyncio.sleep(0.5)

                # 8. 点搜索按钮
                _r2 = await goods_frame.evaluate("""
                    () => {
                        const btn = document.querySelector('div.icon-search');
                        if (btn) {
                            btn.click();
                            return 'clicked';
                        }
                        return 'no-button';
                    }
                """)
                print(f"  -> [搜索按钮] {_r2}")
                await asyncio.sleep(3)

                # 9. 选择第一个“添加”按钮（同 iframe）
                print("  -> 点击列表中第一个“添加”按钮")
                add_btn_in_list = goods_frame.locator('.operate-area .add-img').first
                if await add_btn_in_list.count() > 0:
                    await add_btn_in_list.click(force=True)
                else:
                    btns = goods_frame.locator('div.add-img').all()
                    if btns:
                        await btns[0].click(force=True)
                await asyncio.sleep(2)  # 等待弹窗加载

                # 10. 点击弹窗中的“添加”确认（同 iframe）
                print("  -> 点击弹窗中的“添加”确认")
                confirm_add_btn = goods_frame.locator(
                    'form#goods-form button[type="submit"], form#goods-form button:has-text("添加")').first
                if await confirm_add_btn.count() > 0:
                    await confirm_add_btn.click(force=True)
                else:
                    await goods_frame.locator('button:has-text("添加")').last.click(force=True)
                await asyncio.sleep(2)
                print("  -> 商品插入完成。")

            except Exception as e:
                print(f"插入商品过程出错，跳过商品插入，直接进行后续内链操作：{e}")
        # ================= 新增：商品插入结束 =================

        # ================= 新增：在正文末尾插入6篇内链文章 =================
        print("正文粘贴完成，准备插入6篇内链文章...")
        await asyncio.sleep(random.uniform(1.0, 2.0))

        insert_btn_locator = page.get_by_text("插入", exact=True).first

        try:
            await page.keyboard.press('End')
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"移动光标到末尾失败：{e}")

        for i in range(6):
            print(f"正在插入第 {i + 1}/6 篇文章内链...")

            click_success = False
            try:
                await insert_btn_locator.scroll_into_view_if_needed()
                await asyncio.sleep(0.5)
                await insert_btn_locator.click(force=True)
                click_success = True
            except Exception as e:
                print(f"点击“插入”失败: {e}，尝试鼠标点击兜底...")

            print("  -> 点击成功，等待下拉菜单弹出...")
            await asyncio.sleep(random.uniform(1.0, 1.5))

            # ★ 兜底：如果菜单没出来，用 hover 再展开一次
            if await page.locator('text="超链接"').count() == 0 or \
                    not await page.locator('text="超链接"').first.is_visible():
                print("  -> 菜单未展开，补一次 hover「插入」")
                await insert_btn_locator.hover(force=True)
                await asyncio.sleep(random.uniform(0.8, 1.2))

            if not click_success:
                print("  -> 无法点击“插入”，跳过本篇插入")
                continue


            try:
                await page.evaluate("""
                                    () => {
                                        const holder = document.querySelector('#edui1_iframeholder');
                                        if (holder) {
                                            holder.dataset.__old_pe = holder.style.pointerEvents;
                                            holder.style.pointerEvents = 'none';
                                        }
                                    }
                                    """)

                link_option = page.locator('text="超链接"').first
                # ★ 关键：先等它可见，再把鼠标真的移过去，菜单才不会收
                try:
                    await link_option.wait_for(state='visible', timeout=3000)
                except Exception:
                    print("  -> ⚠️ 未等到「超链接」可见，尝试把鼠标移回「插入」重新展开菜单")
                    await insert_btn_locator.hover(force=True)
                    await asyncio.sleep(random.uniform(0.6, 1.0))

                # 1) 物理鼠标先移动过去（多步移动更接近真人）
                try:
                    box = await link_option.bounding_box()
                    if box:
                        x = box["x"] + box["width"] / 2
                        y = box["y"] + box["height"] / 2
                        await page.mouse.move(x - 20, y, steps=random.randint(5, 10))
                        await asyncio.sleep(random.uniform(0.1, 0.25))
                        await page.mouse.move(x, y, steps=random.randint(3, 6))
                        await asyncio.sleep(random.uniform(0.1, 0.25))
                        await page.mouse.click(x, y)
                    else:
                        # 拿不到坐标时退回 hover + click
                        await link_option.hover(timeout=3000)
                        await asyncio.sleep(random.uniform(0.2, 0.4))
                        await link_option.click(timeout=8000, force=True)
                except Exception as _e:
                    print(f"  -> 物理点击「超链接」失败：{_e}，改 force click")
                    await link_option.click(timeout=8000, force=True)

                try:
                    await page.evaluate("""
                                        () => {
                                            const holder = document.querySelector('#edui1_iframeholder');
                                            if (holder && holder.dataset.__old_pe !== undefined) {
                                                holder.style.pointerEvents = holder.dataset.__old_pe || '';
                                            }
                                        }
                                        """)
                except Exception:
                    pass
                await asyncio.sleep(random.uniform(1.0, 1.5))
            except Exception as e:
                print(f"点击超链接失败：{e}")

                try:
                    await page.evaluate("""
                        () => {
                            const holder = document.querySelector('#edui1_iframeholder');
                            if (holder && holder.dataset.__old_pe !== undefined) {
                                holder.style.pointerEvents = holder.dataset.__old_pe || '';
                            }
                        }
                    """)
                except Exception:
                    pass
                continue

            try:
                await page.locator('.cheetah-tabs-tab-btn', has_text='我的文章').click()
                await asyncio.sleep(random.uniform(1.0, 1.5))
            except Exception as e:
                print(f"点击我的文章失败：{e}")
                continue

            try:
                insert_buttons = page.locator('tr[class*="cheetah-table-row-level-0"] td button', has_text='插入')
                total = await insert_buttons.count()

                if total > 0:
                    target_index = i % total
                    await insert_buttons.nth(target_index).click()
                    print(f"  -> 已选择并插入第 {target_index + 1} 篇文章（共 {total} 篇可选）")
                else:
                    print("  -> 未找到可插入的文章，跳过")
                    continue

                await asyncio.sleep(random.uniform(1.0, 1.5))
            except Exception as e:
                print(f"点击插入失败：{e}")
                continue

            await page.keyboard.press('Shift+Enter')
            await asyncio.sleep(random.uniform(0.5, 1.0))

        print("6 篇文章内链插入完毕，即将进行原有的缩略图操作。")
        await asyncio.sleep(random.uniform(1.5, 2.5))
        # ================= 新增代码结束 =================

        print("检查封面...")
        await asyncio.sleep(random.uniform(1.5, 2.5))
        attempts = 0
        while attempts < 5:
            remove_icons = page.locator('div[class*="removeIcon"]')
            cnt = await remove_icons.count()
            if cnt == 0:
                print("无残留封面。")
                break
            print(f"发现 {cnt} 张封面，清空第一张...")
            try:
                await remove_icons.first.click()
                for _ in range(6):
                    await asyncio.sleep(random.uniform(0.3, 0.8))
                    if await page.locator('div[class*="removeIcon"]').count() < cnt:
                        break
                attempts += 1
            except Exception:
                await asyncio.sleep(random.uniform(0.8, 1.5))
                attempts += 1

        print("设置三图封面...")
        try:
            three = page.locator('text=三图').first
            if await three.count() > 0:
                await three.click()
            else:
                alt = page.locator('[value="three"]').first
                if await alt.count() > 0:
                    await alt.click()
        except Exception as e:
            print(f"三图设置出错：{e}")

        print("点击“选择封面”...")
        await asyncio.sleep(random.uniform(1.5, 2.5))
        try:
            sc = page.locator('text=选择封面').first
            if await sc.count() > 0:
                await sc.click()
            else:
                alt = page.locator('.FeEditorApp__93c3fe2a3121c388-text').first
                if await alt.count() > 0:
                    await alt.click()
        except Exception as e:
            print(f"选择封面出错：{e}")

        print("选第 2、3 张缩略图...")
        await asyncio.sleep(random.uniform(0.8, 1.5))
        try:
            thumbs = page.locator('div[class$="imgItem"]')
            n = await thumbs.count()
            if n >= 3:
                await thumbs.nth(1).click()
                await asyncio.sleep(random.uniform(0.3, 0.8))
                await thumbs.nth(2).click()
                await asyncio.sleep(random.uniform(0.3, 0.8))
            confirm = page.locator('button', has_text='确定').last
            if await confirm.count() > 0:
                await confirm.click()
        except Exception as e:
            print(f"选缩略图出错：{e}")

        await asyncio.sleep(1.5)

        # print("勾选“自动挂载商品”...")
        # await asyncio.sleep(random.uniform(0.8, 1.5))
        # try:
        #     cb = page.locator('#auto_mount_goods').first
        #     if await cb.count() > 0:
        #         try:
        #             checked = await cb.is_checked()
        #         except Exception:
        #             checked = False
        #         if not checked:
        #             await cb.click()
        # except Exception as e:
        #     print(f"自动挂载商品出错：{e}")

        print("检查定时发布...")
        try:
            now = page.locator('text=立即发布').first
            if await now.count() > 0:
                await now.click()
                await asyncio.sleep(random.uniform(0.8, 1.5))
        except Exception:
            pass

        print("定位“发布”按钮...")
        await asyncio.sleep(random.uniform(0.8, 1.5))

        print("检查是否有残留弹窗遮挡...")
        try:
            modal = page.locator('.cheetah-modal-wrap').first
            if await modal.count() > 0 and await modal.is_visible():
                print("发现遮挡弹窗，尝试强制关闭...")
                await page.keyboard.press('Escape')
                await asyncio.sleep(1)
                if await modal.is_visible():
                    await modal.click(force=True, position={'x': 5, 'y': 5})
                    await asyncio.sleep(1)
                if await modal.is_visible():
                    await page.evaluate(
                        "() => { const m = document.querySelector('.cheetah-modal-wrap'); if (m) m.remove(); }")
                    print("已强制移除遮挡弹窗。")
                await asyncio.sleep(1)
            else:
                print("未发现遮挡弹窗。")
        except Exception as e:
            print(f"处理残留弹窗出错：{e}")

        final_btn = page.get_by_text('发布', exact=True).first
        if await final_btn.count() == 0:
            print("未找到发布按钮。")
            return False

        can_pub = await check_publish_quota_via_hover(page, final_btn)
        if not can_pub:
            print("悬停检测：额度 0，暂停。")
            write_task_success()
            return 'PAUSED'

        try:
            await final_btn.click()
            print("已点击“发布”，等待响应...")
        except Exception as e:
            print(f"点击发布出错：{e}")
            return False

        await asyncio.sleep(random.uniform(7, 15))

        try:
            status_btn = page.locator('text=查看发布状态').first
            if await status_btn.count() > 0:
                await status_btn.click(timeout=3000)
                await asyncio.sleep(random.uniform(1.5, 2.5))
        except Exception:
            pass

        if _stop_after_current:
            print("[状态] 最后一篇额度用完，进入暂停。")
            _publish_paused = True
            _pause_reason = 'PLATFORM_ZERO'
            _stop_after_current = False
            _save_persisted_pause()

        print("发布成功，返回 True。")
        return True

    finally:
        if own_context:
            try:
                await camoufox_obj.__aexit__(None, None, None)
                print("[浏览器] Camoufox 已关闭。")
            except Exception as e:
                print(f"[浏览器] 关闭 Camoufox 出错：{e}")
        else:
            print("[浏览器] 外部传入 context，保留不关闭。")

if __name__ == '__main__':
    _ensure_state_loaded()