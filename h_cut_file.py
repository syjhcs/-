import re
import csv
import json
import os


def get_processing_title():
    """从 show_times.json 里找 release_date == '正在处理中' 的电视剧标题。
    找不到返回 None。
    """
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


# ---------- 6. 统计汉字总数并按整条评论切割成多个 CSV ----------

def split_by_chinese(max_chars: int = 1500, min_chars: int = 600):
    current_title = get_processing_title()
    if not current_title:
        raise ValueError("show_times.json 中没有 '正在处理中' 的电视剧")

    json_file = f'{current_title}.json'

    with open(json_file, 'r', encoding='utf-8') as _f:
        _data = json.load(_f)
    _done = [x["id"] for x in _data if x.get("status") == "已抓取"]
    if not _done:
        raise FileNotFoundError(f"{json_file} 中没有已抓取的 id，请先运行抓取脚本")

    current_id = _done[-1]
    latest_csv = f'{current_id}.csv'
    if not os.path.exists(latest_csv):
        raise FileNotFoundError(f"没有找到 CSV 文件：{latest_csv}，请先运行抓取脚本")

    comments = []
    with open(latest_csv, 'r', encoding='utf-8-sig', newline='') as f:
        for row in csv.reader(f):
            if row and row[0].strip():
                comments.append(row[0].strip())

    def _cnt(text):
        return len(re.findall(r'[\u4e00-\u9fa5]', text))

    total = sum(_cnt(c) for c in comments)
    print(f"当前 id：{current_id}，共 {len(comments)} 条评论，汉字总数：{total}")

    if total < min_chars:
        print("文字不足")
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                video_data = json.load(f)

            updated = False
            for item in video_data:
                if isinstance(item, dict) and item.get('id') == current_id:
                    item['split_count'] = 0
                    item['text'] = "文字不足"
                    item.pop('status', None)
                    updated = True
                    break

            if updated:
                with open(json_file, 'w', encoding='utf-8') as f:
                    json.dump(video_data, f, ensure_ascii=False, indent=4)
                print(f"✅ 文字不足，已将 split_count 写为 0，并添加 text 字段为“文字不足”（id={current_id}）")
            else:
                print(f"⚠️ 未找到 id={current_id}，split_count 未更新")

        except Exception as e:
            print(f"⚠️ 更新 {json_file} 失败：{e}")

        return False

    groups = []
    buf, buf_count = [], 0
    for c in comments:
        buf.append(c)
        buf_count += _cnt(c)
        if buf_count >= max_chars:
            groups.append((buf, buf_count))
            buf, buf_count = [], 0

    if buf:
        if groups and buf_count < max_chars:
            prev_buf, prev_count = groups[-1]
            prev_buf.extend(buf)
            groups[-1] = (prev_buf, prev_count + buf_count)
        else:
            groups.append((buf, buf_count))

    for i, (group, cnt) in enumerate(groups, start=1):
        out_name = f'{i}_{current_id}.csv'
        with open(out_name, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            for c in group:
                w.writerow([c])
        print(f"✅ 已生成：{out_name}（{len(group)} 条评论，{cnt} 个汉字）")

    print(f"✅ 切割完成，共生成 {len(groups)} 个文件")

    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            video_data = json.load(f)

        for item in video_data:
            if isinstance(item, dict) and item.get('id') == current_id:
                item['split_count'] = len(groups)
                break

        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(video_data, f, ensure_ascii=False, indent=4)

        print(f"✅ 已将切片数量 {len(groups)} 写入 {json_file}（id={current_id}）")
    except Exception as e:
        print(f"⚠️ 更新 {json_file} 失败：{e}")

    return True