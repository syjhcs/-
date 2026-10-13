import asyncio
import random
import os
import io
import threading
import requests
import numpy as np
import json
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
from rapidocr_onnxruntime import RapidOCR
from invisible_playwright.async_api import InvisiblePlaywright

from z_browser_config import get_profile_dir, get_headless


# ★ 顺序处理，不再并行开页
BATCH_SIZE = 1
# ★ 每处理多少篇重启一次浏览器（Firefox 久跑会衰减）
RESTART_EVERY = 30


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


ocr_engine = RapidOCR()
MIN_ASPECT_RATIO = 1.4
MIN_IMAGE_WIDTH = 300
SKIP_SOURCES = ['海峡都市报', 'CCTV电视剧']
OCR_WORKERS = max(1, int((os.cpu_count() or 4) * 0.75))


def ocr_contains_text(content):
    image = Image.open(io.BytesIO(content))
    result, _ = ocr_engine(np.array(image))
    ocr_text = "".join(line[1] for line in result).replace(" ", "") if result else ""
    return bool(ocr_text)


async def download_article_images(page, start_index, save_dir):
    os.makedirs(save_dir, exist_ok=True)

    try:
        await asyncio.wait_for(
            page.wait_for_selector('#article, div.article', timeout=8000),
            timeout=10,
        )
    except Exception:
        print("未找到文章正文区域，跳过图片下载。")
        return start_index

    article = page.locator('#article').first
    if await article.count() == 0:
        article = page.locator('div.article').first
    if await article.count() == 0:
        print("未找到文章正文区域，跳过图片下载。")
        return start_index

    print("正在滚动页面以确保图片加载完全...")
    try:
        await asyncio.wait_for(
            page.evaluate('window.scrollTo(0, document.body.scrollHeight)'),
            timeout=5,
        )
    except Exception:
        pass
    await asyncio.sleep(2)

    try:
        imgs = await asyncio.wait_for(article.locator('img').all(), timeout=8)
    except Exception:
        imgs = []
    if not imgs:
        print("未在正文中发现任何图片。")
        return start_index

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/126.0.0.0 Safari/537.36',
        'Referer': page.url,
    }

    candidates = []
    for img in imgs:
        try:
            img_url = (await asyncio.wait_for(img.get_attribute('src'), timeout=3)
                       or await asyncio.wait_for(img.get_attribute('data-src'), timeout=3))
            if not img_url or not img_url.startswith('http') or img_url.endswith('.gif'):
                continue

            natural_width = await asyncio.wait_for(
                img.evaluate('el => el.naturalWidth'), timeout=3)
            natural_height = await asyncio.wait_for(
                img.evaluate('el => el.naturalHeight'), timeout=3)
            if not natural_width or not natural_height:
                continue

            natural_width = int(natural_width)
            natural_height = int(natural_height)
            if natural_height <= 0 or natural_width < MIN_IMAGE_WIDTH:
                continue

            ratio = natural_width / natural_height
            if ratio < MIN_ASPECT_RATIO:
                continue

            ext = img_url.split('.')[-1].split('?')[0]
            if ext.lower() not in ['jpg', 'jpeg', 'png', 'webp']:
                ext = 'jpg'

            candidates.append((img_url, natural_width, natural_height, ratio, ext))
        except Exception:
            continue

    if not candidates:
        print("未在正文中发现符合条件的图片。")
        return start_index

    current_index = [start_index]
    index_lock = threading.Lock()

    def process_one(img_url, natural_width, natural_height, ratio, ext):
        try:
            print(f"正在下载 [尺寸: {natural_width}x{natural_height}, "
                  f"比例: {ratio:.2f}] {img_url}")
            resp = requests.get(img_url, headers=headers, timeout=10)
            if resp.status_code != 200:
                print(f"下载失败，状态码: {resp.status_code}")
                return
            try:
                if ocr_contains_text(resp.content):
                    print(f"跳过图片(OCR检测到包含文字): {img_url}")
                    return
                print("OCR检测通过，准备保存...")
            except Exception as ocr_err:
                print(f"OCR检测出错，为保险起见默认保存: {ocr_err}")

            with index_lock:
                file_name = f"{str(current_index[0]).zfill(2)}.{ext}"
                file_path = os.path.join(save_dir, file_name)
                current_index[0] += 1

            with open(file_path, 'wb') as f:
                f.write(resp.content)
            print(f"已保存: {file_name}")
        except Exception as e:
            print(f"处理图片出错: {e}")

    with ThreadPoolExecutor(max_workers=OCR_WORKERS) as executor:
        for img_url, w, h, ratio, ext in candidates:
            executor.submit(process_one, img_url, w, h, ratio, ext)

    print(f"--- 本篇完毕，下一张图将从 {str(current_index[0]).zfill(2)} 开始命名 ---")
    return current_index[0]


async def _collect_links(page, tvname):
    base_url = "https://search.sina.com.cn/search?q=" + tvname + "&tp=news&page={}"
    total_results = []

    for p in range(1, 50):
        print(f"\n{'=' * 20} 正在抓取第 {p} 页 {'=' * 20}")
        try:
            await asyncio.wait_for(
                page.goto(base_url.format(p), wait_until='commit'),
                timeout=20,
            )
        except Exception as e:
            print(f"第 {p} 页打开失败：{e}")
            continue
        await asyncio.sleep(2)

        empty = page.locator('p.ant-empty-description')
        try:
            if await empty.count() > 0:
                text = await empty.first.inner_text()
                if '未找到相关结果' in text:
                    print(f"第 {p} 页显示【未找到相关结果】，停止翻页。")
                    break
        except Exception:
            pass

        try:
            await asyncio.wait_for(
                page.wait_for_selector('div.result-title a', timeout=10000),
                timeout=12,
            )
        except Exception:
            print(f"第 {p} 页等待结果元素加载超时。")
            continue

        try:
            links = await page.locator('div.result-title a').all()
        except Exception:
            links = []

        for element in links:
            try:
                source_ele = element.locator(
                    'xpath=ancestor::div[contains(@class, "result-content-wrapper")]'
                    '//span[contains(@class, "source")]'
                ).first
                if await source_ele.count() > 0:
                    src_text = (await source_ele.inner_text()) or ''
                    if any(s in src_text for s in SKIP_SOURCES):
                        continue
                href = await element.get_attribute('href')
                title_text = await element.inner_text()
                if href:
                    total_results.append({"title": title_text, "url": href})
            except Exception:
                continue

        if p < 2:
            await asyncio.sleep(random.uniform(2.0, 4.0))

    return total_results


# ================= 用独立函数包裹每篇处理 =================
async def _process_one_article(browser, item, idx, total, save_dir, global_img_counter):
    """开一个 page 处理一篇文章，返回新的 global_img_counter。"""
    overall_index = idx

    new_page = None
    try:
        try:
            new_page = await asyncio.wait_for(browser.new_page(), timeout=15)
        except Exception as e:
            print(f"[{overall_index}/{total}] 开新页失败，跳过：{e}")
            return global_img_counter

        print(f"\n{'=' * 50}")
        print(f"【正在处理第 {overall_index}/{total} 篇】")
        print(f"标题: {item['title']}")
        print(f"网址: {item['url']}")
        print(f"{'=' * 50}")

        try:
            await asyncio.wait_for(
                new_page.goto(item['url'], wait_until='commit'),
                timeout=20,
            )
        except Exception as e:
            print(f"打开失败 {item['url']}: {e}")
            return global_img_counter

        try:
            await asyncio.wait_for(
                new_page.wait_for_load_state('domcontentloaded', timeout=8000),
                timeout=10,
            )
        except Exception:
            pass

        await asyncio.sleep(random.uniform(1.0, 2.0))

        try:
            global_img_counter = await asyncio.wait_for(
                download_article_images(new_page, global_img_counter, save_dir),
                timeout=120,
            )
        except asyncio.TimeoutError:
            print(f"[{overall_index}/{total}] 下载图片超时，跳过本篇")
        except Exception as e:
            print(f"[{overall_index}/{total}] 处理图片出错：{e}")

        return global_img_counter

    finally:
        if new_page is not None:
            try:
                await asyncio.wait_for(new_page.close(), timeout=5)
            except Exception:
                pass


async def scrape_and_open_links(browser=None):
    own_pw = None
    if browser is None:
        print("正在启动浏览器...")
        own_pw = InvisiblePlaywright(seed=42, profile_dir=get_profile_dir('sina'), headless=get_headless())
        browser = await own_pw.__aenter__()

    try:
        tvname = get_processing_title()
        if not tvname:
            print("tv_name.json 中没有 '正在处理中' 的电视剧，退出。")
            return

        save_dir = tvname
        print(f"当前剧名：{tvname}，图片将保存到目录：{save_dir}")

        # ---------- 1. 搜索页收集链接 ----------
        search_page = await browser.new_page()
        try:
            total_results = await _collect_links(search_page, tvname)
        finally:
            try:
                await asyncio.wait_for(search_page.close(), timeout=5)
            except Exception:
                pass

        print(f"\n抓取结束，共获取 {len(total_results)} 条链接。准备抓图...")

        global_img_counter = 1
        filtered = [item for item in total_results if tvname in item['title']]
        print(f"过滤标题后剩 {len(filtered)} 条，开始逐篇抓图...")

        # ---------- 2. 【顺序】逐篇处理 ----------
        for i, item in enumerate(filtered, 1):
            # ★ 每 RESTART_EVERY 篇重启一次浏览器
            if own_pw is not None and i > 1 and (i - 1) % RESTART_EVERY == 0:
                print(f"\n=== 已处理 {i - 1} 篇，重启浏览器 ===")
                try:
                    await own_pw.__aexit__(None, None, None)
                except Exception:
                    pass
                await asyncio.sleep(2)
                # ★ 关键修复：这里原来用的 SINA_PROFILE_DIR 已经不存在了
                own_pw = InvisiblePlaywright(seed=42, profile_dir=get_profile_dir('sina'), headless=get_headless())
                browser = await own_pw.__aenter__()

            global_img_counter = await _process_one_article(
                browser, item, i, len(filtered), save_dir, global_img_counter
            )

            await asyncio.sleep(random.uniform(1.0, 2.0))

        print("\n" + "=" * 50)
        print(f"所有链接处理完毕！共抓取图片 {global_img_counter - 1} 张。")
        print(f"所有图片已连续编号并保存到 {save_dir} 文件夹中。")
        print("=" * 50)

    finally:
        if own_pw is not None:
            try:
                await own_pw.__aexit__(None, None, None)
            except Exception:
                pass


if __name__ == '__main__':
    try:
        asyncio.run(scrape_and_open_links())
    except KeyboardInterrupt:
        print("已退出")