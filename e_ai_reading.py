import os
import re
import json
from openai import OpenAI
from dotenv import load_dotenv
from invisible_playwright import InvisiblePlaywright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(BASE_DIR, "tv_name.json"), "r", encoding="utf-8") as f:
    shows = json.load(f)

SHOW_NAME = next(s["title"] for s in shows if s["release_date"] == "正在处理中")
print(f"当前处理：《{SHOW_NAME}》")

OUTPUT_FILE = os.path.join(BASE_DIR, f"{SHOW_NAME}.txt")


def crawl():
    print("开始采集...")
    all_text = []
    with InvisiblePlaywright() as browser:
        page = browser.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto("https://www.tvmao.com/kanju/IWFiYGlr/episode")
        page.wait_for_selector(".epi_t", timeout=15000)
        while True:
            title = page.locator(".epi_t").inner_text()
            print(f"采集：{title}")
            text = page.locator("article.epi_c").inner_text()
            for pat in [r'.*图片版权.*\n?', r'.*上传剧照图片.*\n?', r'.*未经许可.*转载许可.*\n?']:
                text = re.sub(pat, '', text)
            all_text.append(f"========== {title} ==========\n{text.strip()}")

            btn = page.locator("a.epino_btn.rt")
            if not btn.count():
                break
            m = re.search(r'\((\d+)集\)', btn.inner_text())
            if m and int(m.group(1)) > 47:
                break

            old_title = title
            btn.evaluate("el => el.click()")

            new_title = None
            for _ in range(30):
                page.wait_for_timeout(500)
                if page.locator(".epi_t").count():
                    try:
                        t = page.locator(".epi_t").inner_text()
                        if t != old_title:
                            new_title = t
                            break
                    except Exception:
                        continue
            if new_title is None:
                print("  翻页超时，可能已到最后一集，停止")
                break
    print("采集完成")
    return "\n\n".join(all_text)


def summarize(text):
    load_dotenv()
    client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com")

    print(f"读取 {len(text)} 字符")

    eps = [e.strip() for e in re.split(r'(?=^========== 第)', text, flags=re.MULTILINE) if e.strip()]
    groups = [eps[i:i+5] for i in range(0, len(eps), 5)]
    print(f"共 {len(eps)} 集，分 {len(groups)} 组\n")

    partial = []
    for idx, g in enumerate(groups):
        rng = f"第{idx*5+1}-{min((idx+1)*5, len(eps))}集"
        print(f"[{idx+1}/{len(groups)}] 分析 {rng}...")
        r = client.chat.completions.create(
            model="deepseek-flash",
            messages=[
                {"role": "system", "content": (
                    "你是小说编辑。请详细梳理以下剧集的情节，输出内容包括：\n"
                    "1. 本段核心事件（按时间顺序）\n"
                    "2. 出场人物及关系变化\n"
                    "3. 关键对话或细节（可引用原文）\n"
                    "4. 本段埋下或呼应的伏笔\n"
                    "字数不少于 1500 字。"
                )},
                {"role": "user", "content": "\n\n".join(g)}
            ],
            max_tokens=8192
        )
        partial.append(f"### {rng}\n{r.choices[0].message.content}")

    print(f"\n所有分组分析完成，开始统一整合...")
    combined = "\n\n".join(partial)
    print(f"合并素材共 {len(combined)} 字符")

    final = client.chat.completions.create(
        model="deepseek-flash",
        messages=[
            {"role": "system", "content": (
                "你是资深小说编辑。用户会给你一部长篇小说的分段剧情分析素材，"
                "请整合成一份完整、详细、有深度的故事脉络文档。\n\n"
                "【输出结构】\n"
                "一、故事总览（时代背景、主线剧情，500字以上）\n"
                "二、主要人物档案（不少于 10 人：身份、性格、命运走向）\n"
                "三、故事脉络（按序幕/发展/转折/高潮/结局分阶段详述，含关键事件）\n"
                "四、人物关系图（文字描述关系及演变）\n"
                "五、人物弧光（每个核心人物的成长轨迹）\n"
                "六、核心矛盾与冲突（家族/朝堂/情感/身份）\n"
                "七、伏笔与呼应\n"
                "八、主题与情感基调\n\n"
                "【要求】语言生动，有具体情节支撑，不少于 8000 字。"
            )},
            {"role": "user", "content": combined}
        ],
        max_tokens=32768
    )

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(final.choices[0].message.content)
    print(f"完成！已保存至 {OUTPUT_FILE}")


if __name__ == "__main__":
    text = crawl()
    summarize(text)