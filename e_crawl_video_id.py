import asyncio
import json
import os
import re
import signal

# ------------------ 信号处理 ------------------
def _install_signal_handlers():
    def _term_handler(signum, frame):
        raise KeyboardInterrupt(f"收到信号 {signum}")
    try:
        signal.signal(signal.SIGTERM, _term_handler)
    except (ValueError, AttributeError, OSError):
        pass


_install_signal_handlers()


# ------------------ 回滚 release_date ------------------
def _rollback_release_date(title):
    try:
        with open('tv_name.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        print(f"[警告] 无法读取 tv_name.json，跳过回滚（title={title}）")
        return

    if not isinstance(data, list):
        return

    changed = False
    for item in data:
        if (isinstance(item, dict) and item.get('title') == title
                and item.get('release_date') == '正在处理中'):
            item['release_date'] = '未知'
            changed = True
            break

    if not changed:
        return

    try:
        with open('tv_name.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
            f.flush()
            os.fsync(f.fileno())
        print(f"[回滚] 《{title}》release_date 已改回 '未知'")
    except OSError as e:
        print(f"[警告] 写入 tv_name.json 失败：{e}")


# ------------------ 装饰器：异常/中断时回滚 ------------------
def _rollback_on_error(func):
    async def wrapper(page, show_title=None):
        try:
            return await func(page, show_title=show_title)
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\n⛔ 检测到中断，准备回滚 release_date...")
            if show_title:
                _rollback_release_date(show_title)
            raise
        except BaseException as e:
            print(f"❌ 抓取过程中出现异常：{e}")
            if show_title:
                _rollback_release_date(show_title)
            raise
    return wrapper


# ------------------ 滚动到底（轮询高度，不硬睡） ------------------
async def _scroll_to_bottom(page, stable_rounds=3, wait_timeout=5.0):
    last_height = 0
    stable = 0
    round_no = 0
    while stable < stable_rounds:
        round_no += 1
        await page.evaluate(
            "() => window.scrollTo(0, document.body.scrollHeight)"
        )
        t0 = asyncio.get_event_loop().time()
        new_height = await page.evaluate("() => document.body.scrollHeight")
        while new_height == last_height and \
                asyncio.get_event_loop().time() - t0 < wait_timeout:
            await asyncio.sleep(0.2)
            new_height = await page.evaluate(
                "() => document.body.scrollHeight"
            )
        if new_height == last_height:
            stable += 1
        else:
            stable = 0
            last_height = new_height
        print(f"  滚动第 {round_no} 次，高度={new_height}，稳定计数={stable}")
    print('页面已加载至底部，停止滚动。')


# ------------------ 主函数 ------------------
@_rollback_on_error
async def crawl_video_id(page, show_title=None):
    """
    接收已筛选好的抖音搜索 page（Playwright 异步 Page），
    滚动到底加载更多视频，提取点赞 > 400 的视频 ID，保存到 剧名.json
    """
    # ---------- 等瀑布流出现（替代固定 sleep 2） ----------
    try:
        await page.wait_for_selector(
            'div[id^="waterfall_item_"]', timeout=10000
        )
    except Exception:
        print("警告：未在 10s 内看到瀑布流卡片，继续尝试……")

    # ---------- 滚动加载 ----------
    await _scroll_to_bottom(page)

    # ---------- 提取卡片，同时缓存 html ----------
    card_locators = page.locator('div[id^="waterfall_item_"]')
    total = await card_locators.count()
    print(f'找到 {total} 个卡片（含相关搜索），开始过滤...')

    video_cards = []  # [(locator, html), ...]
    for i in range(total):
        card = card_locators.nth(i)
        html = await card.inner_html()
        if '相关搜索' in html or 'hwnREEbx' in html:
            continue
        video_cards.append((card, html))

    print(f'过滤“相关搜索”后，剩余 {len(video_cards)} 个视频卡片')

    # ---------- 逐卡片提取 ----------
    video_ids = []
    for idx, (card, html) in enumerate(video_cards):

        # 点赞数：一次 JS 批量取所有 span 文本
        likes = 0
        try:
            span_texts = await card.evaluate(
                "el => Array.from(el.querySelectorAll('span'))"
                ".map(s => s.innerText)"
            )
            for text in span_texts:
                text = (text or '').strip()
                if not text or ':' in text:
                    continue
                m = re.match(r'^(\d+(?:\.\d+)?)(万)?$', text)
                if m:
                    likes = float(m.group(1))
                    if m.group(2):
                        likes *= 10000
                    break
        except Exception:
            pass

        if likes < 400:
            continue

        found = False

        # 方法0：卡片 id 属性
        card_id = await card.get_attribute('id')
        if card_id and card_id.startswith('waterfall_item_'):
            vid = card_id.split('_')[-1]
            if vid.isdigit() and vid not in video_ids:
                video_ids.append(vid)
            found = True
            continue

        # 方法1：a[href] 里的 /video/(\d+)
        a_loc = card.locator('a')
        a_count = await a_loc.count()
        for j in range(a_count):
            href = await a_loc.nth(j).get_attribute('href') or ''
            m = re.search(r'/video/(\d+)', href)
            if m:
                vid = m.group(1)
                if vid not in video_ids:
                    video_ids.append(vid)
                found = True
                break

        # 方法2：data-id / data-e2e
        if not found:
            data_id = (await card.get_attribute('data-id')) or ''
            if not data_id:
                data_id = (await card.get_attribute('data-e2e')) or ''
            if data_id.isdigit():
                if data_id not in video_ids:
                    video_ids.append(data_id)
                found = True

        # 方法3：从缓存的 html 正则
        if not found:
            m = re.search(r'"aweme_id"\s*:\s*"(\d+)"', html)
            if not m:
                m = re.search(r'/video/(\d+)', html)
            if m:
                vid = m.group(1)
                if vid not in video_ids:
                    video_ids.append(vid)
                found = True

        if not found and idx < 3:
            print(f'警告：第 {idx + 1} 个卡片未能提取到 ID，HTML 片段：')
            print(html[:500])

    video_ids = list(dict.fromkeys(video_ids))
    print(f'共提取到，点赞数大于 400 的视频 id 共计 {len(video_ids)} 个')

    # ---------- 合并保存 ----------
    tv_file = f'{show_title}.json'
    try:
        with open(tv_file, 'r', encoding='utf-8') as f:
            local_data = json.load(f)
    except FileNotFoundError:
        local_data = []
    if not isinstance(local_data, list):
        local_data = []

    local_ids = {
        str(item.get('id'))
        for item in local_data
        if isinstance(item, dict) and item.get('id') is not None
    }

    insert_index = len(local_data)
    for i, item in enumerate(local_data):
        if isinstance(item, dict) and item.get('status') == '已抓取':
            insert_index = i + 1

    new_items = []
    for vid in video_ids:
        vid_str = str(vid)
        if vid_str in local_ids:
            continue
        new_items.append({"id": vid_str, "status": "未抓取"})
        local_ids.add(vid_str)

    local_data[insert_index:insert_index] = new_items

    with open(tv_file, 'w', encoding='utf-8') as f:
        json.dump(local_data, f, ensure_ascii=False, indent=4)
        f.flush()
        os.fsync(f.fileno())

    print(f'去重后的视频 ID 已保存至 {tv_file}，新增 {len(new_items)} 个 ID')
    return video_ids