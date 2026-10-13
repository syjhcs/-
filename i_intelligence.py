# i_intelligence.py
import os
import random
import re
import json
import csv
import asyncio
from openai import OpenAI
from dotenv import load_dotenv
from docx import Document
from docx.shared import Pt, Inches
from docx.oxml.ns import qn

load_dotenv()

# 发布开关：True=生成并发布；False=只生成 Word，不发布
PUBLISH_ENABLED = True


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


def get_last_processing_info():
    current_title = get_processing_title()
    if not current_title:
        raise ValueError("show_times.json 中没有 '正在处理中' 的电视剧")

    json_file = f'{current_title}.json'
    if not os.path.exists(json_file):
        raise FileNotFoundError(f"{json_file} 文件不存在")

    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(f"{json_file} 格式错误，应为列表")

    last = None
    for item in data:
        if isinstance(item, dict) and item.get('status') == '已抓取':
            sc = item.get('split_count', 0)
            try:
                sc = int(sc) if sc is not None else 0
            except (TypeError, ValueError):
                sc = 0
            if sc > 0:
                last = item

    if last is None:
        raise ValueError("未找到 status 为 '已抓取' 且 split_count > 0 的视频")
    return last.get('id'), last.get('split_count', 0)


def get_tv_name():
    title = get_processing_title()
    if not title:
        raise ValueError("show_times.json 中没有 '正在处理中' 的电视剧")
    return title


def read_one_slice(video_id, index):
    filename = f'{index}_{video_id}.csv'
    if not os.path.exists(filename):
        print(f"警告：文件 {filename} 不存在，跳过")
        return None

    comments = []
    with open(filename, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.reader(f)
        for row in reader:
            if row and row[0].strip():
                comments.append(row[0].strip())

    if not comments:
        print(f"警告：文件 {filename} 中没有有效评论，跳过")
        return None

    return '\n'.join(comments)


def save_as_word(article, output_file):
    doc = Document()

    style = doc.styles['Normal']
    style.font.name = '微软雅黑'
    style.font.size = Pt(11)
    style._element.rPr.rFonts.set(qn('w:eastAsia'), '微软雅黑')
    style.paragraph_format.space_before = Pt(0)
    style.paragraph_format.space_after = Pt(0)
    style.paragraph_format.line_spacing = 1.0

    image_dir = get_processing_title()
    image_files = []
    if os.path.isdir(image_dir):
        for name in os.listdir(image_dir):
            if name.lower().endswith('.jpg'):
                image_files.append(os.path.join(image_dir, name))
    print(f"{image_dir} 目录共有 {len(image_files)} 张图片文件")

    random.shuffle(image_files)
    image_cursor = 0

    lines = article.split('\n')

    paragraphs = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r'^#+\s*', '', line)
        paragraphs.append(line)

    for idx, line in enumerate(paragraphs):
        p = doc.add_paragraph(line)
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = 1.0

        if idx > 0 and idx < len(paragraphs) - 1:
            if image_files:
                if image_cursor >= len(image_files):
                    random.shuffle(image_files)
                    image_cursor = 0

                img_path = image_files[image_cursor]
                image_cursor += 1

                doc.add_picture(img_path, width=Inches(6))
                last_p = doc.paragraphs[-1]
                last_p.paragraph_format.space_before = Pt(0)
                last_p.paragraph_format.space_after = Pt(0)
                last_p.paragraph_format.line_spacing = 1.0

    doc.save(output_file)
    return output_file


def generate_one_article(client, tv, comments_text):
    system_prompt = "你是一个视频评论整理成可以发表自媒体文章的工具"

    base_instruction = (
        "这些文字是视频的评论文字，必须帮我把段落重新排序，每一个自然段按照合理逻辑重新排序让阅读通顺，这个你绝对不许偷懒"
        "不是评论格式，我要发布自媒体，如果段落和段落之间阅读不通顺，你可以在下一行段落的开头，或者本行的结尾修改汉字，本行和下一行，两个段落修改的文字不能超过10个，目的就是为了让阅读通顺"
       "不可以大段改，改开头或者结尾就行，改动不能超过10个字。"
        "然后再根据文章另外生成标题，标题要有钩子。"

        # ★ 核心修改部分：强化过渡和购买引导
        "特别重要：在文章正文的最后，根据整篇文章，用50-到80字进行合理续写，自然、平滑地过渡到该电视剧的原著书籍"
        "在续写内容中，要强调原著比剧集更加细腻、精彩，留有更多的想象空间，并强烈引导观众购买原著书籍。"
        "必须使用类似如下的引导语激发购买欲：'剧版被改的面目全非，我觉得原著剧情结构和走向我觉得更加合理'、'强烈建议喜欢这部剧的朋友入手一本原著细读'。"
        "这段续写必须作为文章的独立结尾段落，语气要自然、真诚，不要生硬推销。"
        "请按照以下格式输出："
        f"第一行写标题，格式必须是：{tv}: 后面直接跟着具体的标题内容，\n "
        f"例如：{tv}: 逆袭的秘诀，标题中不要带电视剧的名字，必须和电视剧贴合，所有的文章都是电视剧，写文章内容。整行标题（含“{tv}:”前缀）标题汉字数22-60个，不含标点、空格、数字和英文。"
    )

    user_content = base_instruction + "\n\n评论内容：\n" + comments_text

    # 以下代码保持不变...
    response = client.chat.completions.create(
        model="deepseek-flash",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ],
        temperature=0.7,
        max_tokens=100000,
        stream=True,
        reasoning_effort="max",
        extra_body={"thinking": {"type": "enabled"}}
    )

    full_content = []
    last_was_newline = False
    for chunk in response:
        if not getattr(chunk, "choices", None):
            continue
        delta = chunk.choices[0].delta
        if hasattr(delta, "content") and delta.content:
            text = delta.content
            full_content.append(text)

            out_chars = []
            for ch in text:
                if ch == '\n':
                    if last_was_newline:
                        continue
                    last_was_newline = True
                else:
                    last_was_newline = False
                out_chars.append(ch)
            print(''.join(out_chars), end="", flush=True)

    article = "".join(full_content)
    article = re.sub(r'\n\s*\n+', '\n', article)
    article = article.strip()

    if article:
        print()
    return article


# ★ 改成 async def，加 pw 参数
async def generate_article(pw=None):
    """生成一篇切片文章后立即发布，成功发布后更新 published_count。

    pw=None   -> run_once 自己新建浏览器
    pw 传入   -> run_once 复用外部浏览器（谁传谁负责关）
    """
    generated_any = False

    try:
        video_id, split_count = get_last_processing_info()
    except (ValueError, FileNotFoundError) as e:
        print(f"没有可处理的视频，跳过：{e}")
        return generated_any

    print(f"当前处理视频 ID: {video_id}, split_count: {split_count}")

    tv = get_tv_name()
    print(f"电视剧名称: {tv}")

    api_key = os.environ.get('DEEPSEEK_API_KEY')
    if not api_key:
        raise ValueError("未在 .env 中找到 DEEPSEEK_API_KEY，请检查 .env 文件")

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com"
    )

    from j_releasing import (
        run_once,
        get_published_count,
        update_published_count,
        set_published_note,
        mark_published_in_json,
        is_publish_paused,
    )

    current_title = get_processing_title()
    if not current_title:
        raise ValueError("show_times.json 中没有 '正在处理中' 的电视剧")

    JSON_PATH = f'{current_title}.json'

    if PUBLISH_ENABLED:
        already_published = get_published_count(JSON_PATH, video_id)
        i = already_published + 1
        print(f"已发布 {already_published} 篇，本次从第 {i} 篇开始")
    else:
        i = 1
        print("只生成 Word 模式：从第 1 篇开始检查，已存在的 docx 会跳过生成")

    if i > split_count:
        if PUBLISH_ENABLED:
            print("该视频的所有切片已全部发布完毕，标记为已发布。")
            mark_published_in_json(JSON_PATH, video_id)
        else:
            print("该视频的所有切片 Word 都已生成完毕，未发布。")
        return generated_any

    while i <= split_count:
        if PUBLISH_ENABLED and is_publish_paused():
            print("[状态] 检测到已暂停，返回 'PAUSED' 给主流程。")
            return 'PAUSED'

        docx_file = f'{i}_{video_id}.docx'

        if not os.path.exists(docx_file):
            comments_text = read_one_slice(video_id, i)
            if not comments_text:
                print(f"切片 {i} 无有效评论，跳过。")
                i += 1
                continue

            print(f"=== 生成第 {i} 篇（共 {split_count} 篇）===")
            print(f"共读取评论字符数: {len(comments_text)}")
            print("正在调用 DeepSeek API 生成文章...")

            # ★ API 抖动重试 3 次；3 次都失败就跳过本篇
            article = None
            for attempt in range(3):
                try:
                    article = generate_one_article(client, tv, comments_text)
                    break
                except Exception as e:
                    print(f"调用 API 失败（第 {attempt + 1}/3 次）：{e}")
                    if attempt < 2:
                        await asyncio.sleep(5)

            if article is None:
                print(f"调用 API 连续失败 3 次，跳过本篇（切片 {i}）")
                i += 1
                continue

            save_as_word(article, docx_file)
            generated_any = True
            print(f"已生成：{docx_file}")
        else:
            if PUBLISH_ENABLED:
                print(f"{docx_file} 已存在，跳过生成，直接发布")
            else:
                print(f"{docx_file} 已存在，跳过生成")

        if PUBLISH_ENABLED:
            print(f"=== 发布第 {i} 篇（共 {split_count} 篇）===")
            # ★ 关键：await + 透传 pw
            result = await run_once(docx_file, pw=pw)

            if result == 'PAUSED':
                print("发布已暂停（平台额度耗尽），返回 'PAUSED' 给主流程。")
                return 'PAUSED'

            if result == 'STOP':
                update_published_count(JSON_PATH, video_id, i)
                note = f"今天发到第{i}篇被限制，明天从第{i + 1}篇继续"
                set_published_note(JSON_PATH, video_id, note)
                print(f"已到今日发布上限，当前为第 {i} 篇，published_count 已写为 {i}。")
                if os.path.exists(f'{i}_{video_id}.csv'):
                    os.remove(f'{i}_{video_id}.csv')
                if os.path.exists(docx_file):
                    os.remove(docx_file)
                return 'DAILY_LIMIT'

            if result:
                update_published_count(JSON_PATH, video_id, i)
                print(f"第 {i} 篇发布成功，published_count 已更新为 {i}")
                if os.path.exists(f'{i}_{video_id}.csv'):
                    os.remove(f'{i}_{video_id}.csv')
                if os.path.exists(docx_file):
                    os.remove(docx_file)

                if is_publish_paused():
                    print("[状态] 发布成功，但检测到已暂停（平台额度耗尽），返回 'PAUSED' 给主流程。")
                    return 'PAUSED'
            else:
                print(f"第 {i} 篇发布失败，published_count 保持为 {i - 1}，停止后续。")
                raise SystemExit(1)
        else:
            print(f"第 {i} 篇只生成 Word，不发布：{docx_file}")

        i += 1

    if PUBLISH_ENABLED:
        mark_published_in_json(JSON_PATH, video_id)
        print(f"id={video_id} 处理完毕，共计 {split_count} 篇文章发布成功，已标记为 published。")
    else:
        print(f"id={video_id} 的 Word 文档已全部生成完毕，未发布。")

    return generated_any


# ★ 也改成 async def，加 pw 参数
async def resume_unfinished(pw=None):
    from j_releasing import get_last_target_from_video_ids

    current_title = get_processing_title()
    if not current_title:
        return False

    vid, sc = get_last_target_from_video_ids(f'{current_title}.json')

    if vid and sc > 0:
        print(f"=== 发现未发完的视频 {vid}（共 {sc} 篇），先继续生成并发布 ===")
        generated = await generate_article(pw=pw)
        return generated
    return False


if __name__ == '__main__':
    asyncio.run(generate_article())