import asyncio
from invisible_playwright.async_api import InvisiblePlaywright


from z_browser_config import get_profile_dir, get_headless



async def get_iqiyi_titles():
    async with InvisiblePlaywright(seed=42, profile_dir=get_profile_dir('iqiyi'), headless=get_headless()) as browser:
        page = await browser.new_page()
        await page.goto('https://www.iqiyi.com', wait_until='domcontentloaded')
        await page.click('[data-ai-entity="电视剧频道"]')
        await page.wait_for_selector(
            'div[class*="thumbnail_thumbnailItemWrap"]', timeout=15000
        )

        all_titles = []
        last_count = 0

        while True:
            cards = page.locator('div[class*="thumbnail_thumbnailItemWrap"]')
            count = await cards.count()

            for i in range(count):
                card = cards.nth(i)

                link_locator = card.locator('xpath=ancestor::a')
                if await link_locator.count() > 0:
                    href = await link_locator.first.get_attribute('href') or ''
                    if 'www.iqiyi.com' not in href:
                        continue

                title_locator = card.locator(
                    'span.thumbnail_thumbnailItemTitle__2cvhB'
                )
                if await title_locator.count() > 0:
                    text = (await title_locator.first.inner_text()).strip()
                    if text and ('《' in text or '》' in text):
                        continue
                    if text and text not in all_titles:
                        all_titles.append(text)

            if len(all_titles) == last_count:
                break
            last_count = len(all_titles)

            next_btn = page.locator('button[aria-label="下一页"]')
            if await next_btn.count() == 0:
                break
            try:
                await next_btn.first.evaluate('el => el.click()')
                await asyncio.sleep(1)
            except Exception:
                break

        active_locator = page.locator('div[class*="thumbnail_selected"]')
        if await active_locator.count() > 0:
            active_title = active_locator.first.locator(
                'span.thumbnail_thumbnailItemTitle__2cvhB'
            )
            if await active_title.count() > 0:
                active_text = (await active_title.first.inner_text()).strip()
                if active_text in all_titles:
                    idx = all_titles.index(active_text)
                    all_titles = all_titles[idx:] + all_titles[:idx]

        print(f'成功获取到 {len(all_titles)} 个标题！')
        return all_titles