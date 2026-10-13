import asyncio
import random
import csv
import os
import json
import re
import atexit
import time


# ================= 全局：当前处理 id + exit 只注册一次 =================
_current_processing_id = None
_exit_handler_registered = False


# ================= 状态文件相关 =================

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


MAX_FAIL = 10

# ★ 异常结束（断流）时，至少抓到多少条才认为成功
MIN_COMMENTS_ON_ERROR = 5

# ★ 连续失败多少次触发"重启浏览器"
MAX_CONSECUTIVE_EMPTY = 6

# ★ 最多重启几次；重启超过这个次数还失败就跳过 id
MAX_RESTART = 2


def reset_stuck_status():
    current_title = get_processing_title()
    if not current_title:
        return
    json_file = f'{current_title}.json'
    if not os.path.exists(json_file):
        return
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            _ids = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return
    if not isinstance(_ids, list):
        return
    changed = 0
    for _item in _ids:
        if isinstance(_item, dict) and _item.get("status") == "抓取中":
            _item["status"] = "未抓取"
            _item["fail_count"] = _item.get("fail_count", 0) + 1
            changed += 1
    if not changed:
        return
    try:
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(_ids, f, ensure_ascii=False, indent=2)
        print(f"[启动重置] 已把 {changed} 个卡在 '抓取中' 的 id 改回 '未抓取'，fail_count +1")
    except OSError as e:
        print(f"[警告] 重置写入失败：{e}")


def _mark_success(video_id):
    global _current_processing_id
    current_title = get_processing_title()
    if not current_title:
        return
    json_file = f'{current_title}.json'
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            _ids = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return
    if not isinstance(_ids, list):
        return
    changed = False
    for _item in _ids:
        if isinstance(_item, dict) and _item.get("id") == video_id:
            if _item.get("status") == "抓取中":
                _item["status"] = "已抓取"
                changed = True
            break
    if not changed:
        return
    try:
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(_ids, f, ensure_ascii=False, indent=2)
        print(f"[完成] id={video_id} 状态已标记为 '已抓取'")
    except OSError as e:
        print(f"[警告] 写入 {json_file} 失败：{e}")
    if _current_processing_id == video_id:
        _current_processing_id = None


def _pick_next_id():
    current_title = get_processing_title()
    if not current_title:
        return None
    json_file = f'{current_title}.json'
    if not os.path.exists(json_file):
        return None
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            ids = json.load(f)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(ids, list):
        return None

    def is_candidate(item):
        return (isinstance(item, dict)
                and item.get("status") == "未抓取"
                and item.get("published") != "已发布"
                and item.get("text") != "文字不足"
                and item.get("fail_count", 0) < MAX_FAIL)

    candidates = [x for x in ids if is_candidate(x)]
    if candidates:
        candidates.sort(key=lambda x: x.get("fail_count", 0))
        return candidates[0].get("id")
    return None


def _rollback_status(video_id):
    global _current_processing_id
    current_title = get_processing_title()
    if not current_title:
        return
    json_file = f'{current_title}.json'
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            _ids = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return
    if not isinstance(_ids, list):
        return
    changed = False
    for _item in _ids:
        if isinstance(_item, dict) and _item.get("id") == video_id:
            if _item.get("status") == "抓取中":
                _item["status"] = "未抓取"
                _item["fail_count"] = _item.get("fail_count", 0) + 1
                changed = True
            break
    if not changed:
        return
    try:
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(_ids, f, ensure_ascii=False, indent=2)
        print(f"[回滚] id={video_id} 状态已改回 '未抓取'，fail_count 已累加")
    except OSError as e:
        print(f"[警告] 写入失败：{e}")
    if _current_processing_id == video_id:
        _current_processing_id = None


def _exit_rollback():
    if _current_processing_id:
        print(f"[退出钩子] 进程退出，回滚 id={_current_processing_id}")
        _rollback_status(_current_processing_id)


def _mark_done(video_id):
    global _current_processing_id, _exit_handler_registered
    current_title = get_processing_title()
    if not current_title:
        return
    json_file = f'{current_title}.json'
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            _ids = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return
    if not isinstance(_ids, list):
        return
    changed = False
    for _item in _ids:
        if isinstance(_item, dict):
            if _item.get("id") == video_id and _item.get("status") == "未抓取":
                _item["status"] = "抓取中"
                changed = True
                break
        elif isinstance(_item, str) and _item == video_id:
            idx = _ids.index(_item)
            _ids[idx] = {"id": video_id, "status": "抓取中"}
            changed = True
            break
    if not changed:
        return
    try:
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(_ids, f, ensure_ascii=False, indent=2)
    except OSError:
        return

    _current_processing_id = video_id
    if not _exit_handler_registered:
        atexit.register(_exit_rollback)
        _exit_handler_registered = True


# ================= 工具 =================

async def random_delay(min_s=0.3, max_s=1.2):
    await asyncio.sleep(random.uniform(min_s, max_s))


def extract_comments(json_data):
    comments = []
    if not isinstance(json_data, dict):
        return comments
    for key in ['comments', 'comment_list']:
        items = json_data.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    text = item.get('text', '').strip()
                    if text:
                        comments.append(text.replace('\n', ' '))
            if comments:
                return comments
    data = json_data.get('data')
    if isinstance(data, dict):
        inner = extract_comments(data)
        if inner:
            return inner
    if isinstance(json_data, list):
        for item in json_data:
            if isinstance(item, dict) and 'text' in item:
                t = item['text'].strip()
                if t:
                    comments.append(t.replace('\n', ' '))
    return comments


def count_chinese_chars(text):
    return len(re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf]', text))


def has_repeated_chars(text, repeat=5):
    return bool(re.search(r'([\u4e00-\u9fa5])\1{' + str(repeat - 1) + r',}', text))


# ================= DOM 清理 JS 脚本（常量） =================

JS_HOLLOW_COMMENTS = """
(function(){
    var sels = [
        '[data-e2e*="comment-item"]',
        '[class*="commentItem"]',
        '[class*="CommentItem"]',
        '[class*="comment-item"]'
    ];
    var nodes = document.querySelectorAll(sels.join(','));
    var cleared = 0;
    for (var i = 0; i < nodes.length; i++) {
        var el = nodes[i];
        if (el.dataset && el.dataset.__hollowed) continue;
        var r = el.getBoundingClientRect();
        if (r.bottom < 0) {
            el.innerHTML = '';
            if (el.dataset) el.dataset.__hollowed = '1';
            cleared++;
        }
    }
    return cleared;
})();
"""

JS_SLIM_COMMENTS = """
(function(maxKeep){
    var sels = [
        '[data-e2e*="comment-item"]',
        '[class*="commentItem"]',
        '[class*="CommentItem"]',
        '[class*="comment-item"]'
    ];
    var nodes = document.querySelectorAll(sels.join(','));
    var arr = Array.prototype.slice.call(nodes);
    if (arr.length <= maxKeep) return 0;

    var toProcess = arr.slice(0, arr.length - maxKeep);
    var processed = 0;
    for (var i = 0; i < toProcess.length; i++) {
        var el = toProcess[i];
        if (el.dataset && el.dataset.__slimmed) continue;
        var h = el.offsetHeight;
        if (!h || h < 5) continue;
        var ph = document.createElement('div');
        ph.style.height = h + 'px';
        ph.style.width = '100%';
        ph.style.pointerEvents = 'none';
        ph.style.background = 'transparent';
        if (ph.dataset) ph.dataset.__slimmed = '1';
        if (el.parentNode) {
            el.parentNode.replaceChild(ph, el);
            processed++;
        }
    }
    return processed;
})(%d);
"""

JS_CLEAN_MEDIA = """
(function(){
    var nodes = document.querySelectorAll('img, video');
    var cleaned = 0, maxClean = 20;
    for (var i = 0; i < nodes.length && cleaned < maxClean; i++){
        var r = nodes[i].getBoundingClientRect();
        if (r.bottom < -2000 || r.top > window.innerHeight + 2000){
            if (nodes[i].tagName === 'IMG'){
                nodes[i].src = '';
                nodes[i].removeAttribute('srcset');
                nodes[i].style.visibility = 'hidden';
            } else {
                try { nodes[i].pause(); } catch(e) {}
                nodes[i].removeAttribute('src');
                try { nodes[i].load(); } catch(e) {}
                nodes[i].style.visibility = 'hidden';
            }
            cleaned++;
        }
    }
    return cleaned;
})();
"""


# ================= 在页面里用 fetch 调评论接口（含自动重试） =================

async def _fetch_comment_page(page, video_id, cursor, count=20, max_retry=2):
    last_err = None
    for attempt in range(max_retry + 1):
        try:
            result = await page.evaluate("""
                async (args) => {
                    const [vid, cursor, count] = args;
                    const url = 'https://www.douyin.com/aweme/v1/web/comment/list/'
                        + '?aweme_id=' + encodeURIComponent(vid)
                        + '&cursor=' + cursor
                        + '&count=' + count
                        + '&item_type=0'
                        + '&insert_ids='
                        + '&whale_cut_token='
                        + '&cut_version=1'
                        + '&device_platform=webapp'
                        + '&aid=6383'
                        + '&channel=channel_pc_web';
                    try {
                        const resp = await fetch(url, {
                            method: 'GET',
                            credentials: 'include',
                            headers: {
                                'Accept': 'application/json, text/plain, */*'
                            }
                        });
                        if (!resp.ok) {
                            return {__error: 'HTTP ' + resp.status};
                        }
                        const text = await resp.text();
                        try {
                            return JSON.parse(text);
                        } catch (e) {
                            return {__error: 'JSON parse failed: ' + text.slice(0, 200)};
                        }
                    } catch (e) {
                        return {__error: String(e)};
                    }
                }
            """, [video_id, cursor, count])

            if not (isinstance(result, dict) and "__error" in result):
                return result

            last_err = result["__error"]

            if attempt < max_retry:
                print(f"  ↻ 通道错误，重试 {attempt + 1}/{max_retry}...")
                await asyncio.sleep(random.uniform(0.8, 1.5))
                continue
            return result

        except Exception as e:
            last_err = str(e)
            if attempt < max_retry:
                print(f"  ↻ 异常，重试 {attempt + 1}/{max_retry}...")
                await asyncio.sleep(random.uniform(0.8, 1.5))
                continue
            return {"__error": last_err}
    return {"__error": last_err or "unknown"}


# ================= 核心抓取（页面生命周期 + API 直连） =================

async def grab_comments(browser, vid_url, txt_file, video_id,
                        restart_every=1500, keep_recent_nodes=400,
                        count_per_page=20,
                        cursor_hard_limit=1000000):
    """
    ★ 返回 (total_count, end_reason)

    end_reason 取值：
      'has_more_0'      -> has_more=0，抖音明确说没有下一页（唯一正常结束）
      'cursor_limit'    -> cursor 达到硬上限
      'request_failed'  -> 连续 N 次请求失败
      'status_error'    -> 连续 N 次 status_code != 0
      'empty_6'         -> 连续 N 次空响应
      'cursor_stuck'    -> cursor 不前进
      'goto_failed'     -> 打开视频页失败
    """
    start_time = time.time()
    seen_comments = set()
    total_count = 0
    cursor = 0
    page = None
    pages_since_restart = 0
    consecutive_empty = 0
    finished_normally = False
    end_reason = 'unknown'

    try:
        file_mode = 'w'

        with open(txt_file, file_mode, encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)

            while True:
                if page is not None:
                    try:
                        await page.close()
                    except Exception:
                        pass
                    await asyncio.sleep(random.uniform(1.0, 2.0))
                    print(f"🔄 重启 page（累计 {total_count} 条）")

                page = await browser.new_page()

                try:
                    await page.add_init_script("""
                        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                        Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh']});
                    """)
                except Exception as e:
                    print(f"注入反检测脚本失败（不影响抓取）：{e}")

                async def _block(route):
                    try:
                        url = route.request.url
                        if re.search(r'\.(mp4|webm|m4s|ts)(\?|$)', url):
                            await route.abort()
                        else:
                            await route.continue_()
                    except Exception:
                        try:
                            await route.continue_()
                        except Exception:
                            pass

                try:
                    await page.route('**/*', _block)
                except Exception:
                    pass

                try:
                    await page.goto(vid_url, wait_until='domcontentloaded',
                                    timeout=30000)
                except Exception as e:
                    print(f"打开视频页失败：{e}")
                    end_reason = 'goto_failed'
                    return total_count, end_reason

                await asyncio.sleep(random.uniform(1.0, 2.0))
                real_url = page.url
                if '/note/' in real_url and '/video/' in vid_url:
                    note_url = vid_url.replace('/video/', '/note/')
                    print(f"↪ 检测到笔记类型，自动切换：{vid_url} -> {note_url}")
                    try:
                        await page.goto(note_url, wait_until='domcontentloaded',
                                        timeout=30000)
                    except Exception as e:
                        print(f"打开笔记页失败：{e}")
                        end_reason = 'goto_failed'
                        return total_count, end_reason
                elif '/note/' in real_url:
                    print(f"↪ 当前已是笔记页：{real_url}")

                await asyncio.sleep(random.uniform(3.0, 4.5))

                try:
                    await page.evaluate('window.scrollBy(0, 700)')
                    await asyncio.sleep(random.uniform(1.5, 2.5))
                    await page.evaluate('window.scrollBy(0, -400)')
                    await asyncio.sleep(random.uniform(1.0, 1.8))
                except Exception:
                    pass

                pages_since_restart = 0
                page_start_count = total_count

                while True:
                    if restart_every and (total_count - page_start_count) >= restart_every:
                        print(f"🔄 本页已收 {total_count - page_start_count} 条新评论，"
                              f"触发页面重启（累计 {total_count} 条）...")
                        break

                    if cursor >= cursor_hard_limit:
                        print(f"⚠️ cursor 达到硬上限 {cursor_hard_limit}，停止抓取")
                        finished_normally = True
                        end_reason = 'cursor_limit'
                        break

                    data = await _fetch_comment_page(
                        page, video_id, cursor, count_per_page
                    )

                    if isinstance(data, dict) and "__error" in data:
                        err = data["__error"]
                        print(f"⚠️ cursor={cursor} 请求失败：{err}")
                        consecutive_empty += 1
                        if consecutive_empty >= 2 and consecutive_empty % 2 == 0:
                            print(f"  ⏸️ 连续失败 {consecutive_empty} 次，休息 10 秒...")
                            await asyncio.sleep(10)
                        if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                            print(f"连续 {MAX_CONSECUTIVE_EMPTY} 次请求失败，停止")
                            finished_normally = True
                            end_reason = 'request_failed'
                            break
                        await asyncio.sleep(random.uniform(2.5, 4.0))
                        continue

                    status_code = (data.get("status_code", 0)
                                   if isinstance(data, dict) else -1)
                    if status_code != 0:
                        print(f"⚠️ cursor={cursor} status_code={status_code}")
                        consecutive_empty += 1
                        if consecutive_empty >= 2 and consecutive_empty % 2 == 0:
                            print(f"  ⏸️ 连续失败 {consecutive_empty} 次，休息 5 秒...")
                            await asyncio.sleep(5)
                        if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                            print(f"连续 {MAX_CONSECUTIVE_EMPTY} 次异常响应，停止")
                            finished_normally = True
                            end_reason = 'status_error'
                            break
                        await asyncio.sleep(random.uniform(2.5, 4.0))
                        continue

                    comments = data.get("comments") or []
                    has_more = data.get("has_more", 0)
                    next_cursor = data.get("cursor", cursor)

                    new_added = 0
                    if comments:
                        consecutive_empty = 0
                        for item in comments:
                            if not isinstance(item, dict):
                                continue
                            t = (item.get("text") or "").strip()
                            if not t:
                                continue
                            t = t.replace("\n", " ")
                            if count_chinese_chars(t) < 80:
                                continue
                            if has_repeated_chars(t):
                                continue
                            if t in seen_comments:
                                continue
                            seen_comments.add(t)
                            writer.writerow([t])
                            f.flush()
                            total_count += 1
                            new_added += 1
                        if new_added > 0:
                            print(f"📄 cursor={cursor} +{new_added}"
                                  f"（累计 {total_count}，next_cursor={next_cursor}）")
                    else:
                        # ★ 空响应里，如果 has_more=0，说明已经到底了，是正常结束
                        if not has_more:
                            print(f"🎉 空响应且 has_more=0，正常抓完，共 {total_count} 条")
                            finished_normally = True
                            end_reason = 'has_more_0'
                            break
                        consecutive_empty += 1
                        print(f"📄 cursor={cursor} 空响应"
                              f"（连续 {consecutive_empty} 次）")
                        if consecutive_empty >= 2 and consecutive_empty % 2 == 0:
                            print(f"  ⏸️ 连续失败 {consecutive_empty} 次，休息 5 秒...")
                            await asyncio.sleep(5)
                        if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                            print(f"连续 {MAX_CONSECUTIVE_EMPTY} 次空响应，停止")
                            finished_normally = True
                            end_reason = 'empty_6'
                            break

                    if next_cursor == cursor:
                        print(f"⚠️ cursor 不前进（{cursor}），停止")
                        finished_normally = True
                        end_reason = 'cursor_stuck'
                        break

                    cursor = next_cursor
                    pages_since_restart += 1

                    await asyncio.sleep(random.uniform(1.5, 3.0))

                if finished_normally:
                    break

    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass

    elapsed = time.time() - start_time
    print(f"[耗时] 抓取 {total_count} 条评论，用时 {elapsed:.1f}s，结束原因：{end_reason}")

    return total_count, end_reason


# ================= 单个视频入口 =================

async def crawl_comment(browser, force_video_id=None, restart_count=0):
    """
    抓一个视频的评论。

    参数：
      - force_video_id=None  → 正常从 JSON 挑一个"未抓取"的 id
      - force_video_id=xxx   → 直接用 xxx 这个 id（重启重抓场景，不改状态、不调 reset）
      - restart_count        → 已经重启几次（0/1/2）

    返回值：
      - "NO_MORE"                              → 没有待抓取的 id
      - "SUCCESS"                              → 抓取成功
      - "FAILED"                               → 抓取失败（已回滚）
      - ("NEED_RESTART", video_id, new_rc)     → 需要重启浏览器，用同一个 id 重抓
    """
    if force_video_id:
        video_id = force_video_id
        print(f"[重抓] 继续抓取 id={video_id}（已重启 {restart_count} 次）")
    else:
        reset_stuck_status()
        video_id = _pick_next_id()
        if video_id is None:
            print("没有待抓取的视频（文件不存在 / 空 / 全部已处理）")
            return "NO_MORE"
        _mark_done(video_id)

    vid_url = f'https://www.douyin.com/video/{video_id}'
    print("正在处理：" + vid_url)
    csv_file = f'{video_id}.csv'

    await asyncio.sleep(random.uniform(0.3, 0.8))

    try:
        total_count, end_reason = await grab_comments(
            browser, vid_url, csv_file, video_id
        )

        if end_reason == 'has_more_0':
            if total_count > 0:
                print(f"✅ 正常抓完（has_more=0），共 {total_count} 条，保存至 {csv_file}")
                _mark_success(video_id)
                return "SUCCESS"
            else:
                print("❌ 正常抓完但 0 条评论，判为失败")
                _rollback_status(video_id)
                if os.path.exists(csv_file):
                    try:
                        os.remove(csv_file)
                    except OSError:
                        pass
                return "FAILED"

        # 异常结束（断流/风控/超时）
        if total_count >= MIN_COMMENTS_ON_ERROR:
            print(f"⚠️ 异常结束（{end_reason}），但已抓到 {total_count} 条"
                  f"（≥{MIN_COMMENTS_ON_ERROR}），判为成功")
            _mark_success(video_id)
            return "SUCCESS"

        # ★ 连续失败 4 次：可能触发重启
        if end_reason in ('request_failed', 'status_error', 'empty_6'):
            if restart_count < MAX_RESTART:
                new_rc = restart_count + 1
                print(f"🔄 连续失败结束（{end_reason}），准备重启浏览器"
                      f"（第 {new_rc}/{MAX_RESTART} 次），继续抓 id={video_id}")
                return ("NEED_RESTART", video_id, new_rc)
            else:
                print(f"❌ 已重启 {MAX_RESTART} 次仍失败（{end_reason}），跳过 id={video_id}")
                _rollback_status(video_id)
                if os.path.exists(csv_file):
                    try:
                        os.remove(csv_file)
                    except OSError:
                        pass
                return "FAILED"

        # 其他异常结束（cursor_limit / cursor_stuck / goto_failed）
        print(f"❌ 异常结束（{end_reason}），只抓到 {total_count} 条"
              f"（<{MIN_COMMENTS_ON_ERROR}），判为失败")
        _rollback_status(video_id)
        if os.path.exists(csv_file):
            try:
                os.remove(csv_file)
            except OSError:
                pass
        return "FAILED"

    except Exception as e:
        print(f"❌ 抓取过程中出现异常：{e}")
        _rollback_status(video_id)
        if os.path.exists(csv_file):
            try:
                os.remove(csv_file)
            except OSError:
                pass
        return "FAILED"


if __name__ == '__main__':
    import sys
    from invisible_playwright.async_api import InvisiblePlaywright
    from z_browser_config import get_profile_dir, get_headless

    async def _debug():
        async with InvisiblePlaywright(
            seed=42, profile_dir=get_profile_dir('douyin_cmt'), headless=get_headless()
        ) as browser:
            await crawl_comment(browser)

    asyncio.run(_debug())