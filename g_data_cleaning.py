import os
import json
import csv
import re


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


# ---------- 1. 获取最新的 CSV 文件 ----------

def filter_comment():
    # 从当前剧名 json 中取出最后一条“抓取中”的 ID
    current_title = get_processing_title()
    if not current_title:
        raise ValueError("show_times.json 中没有 '正在处理中' 的电视剧")

    json_file = f'{current_title}.json'

    with open(json_file, 'r', encoding='utf-8') as _f:
        _data = json.load(_f)
    _done = [x["id"] for x in _data if x.get("status") == "已抓取"]
    if not _done:
        raise FileNotFoundError(f"{json_file} 中没有已抓取的 id，请先运行抓取脚本")

    latest_csv = f'{_done[-1]}.csv'
    if not os.path.exists(latest_csv):
        raise FileNotFoundError(f"没有找到 CSV 文件：{latest_csv}，请先运行抓取脚本")
    print(f"正在处理文件：{latest_csv}")

    # ---------- 2. 定义需要删除的符号模式（正则） ----------
    pattern = re.compile(r'\[[^\[\]]+\]')   # 匹配中括号及其内部内容

    # 白名单，只保留中文、英文、数字、常用标点和空格，其余（含 emoji）全删
    keep_pattern = re.compile(
        r'[^\u4e00-\u9fff\u3400-\u4dbf0-9a-zA-Z'
        r'，。！？、；：""''（）《》【】\-—…,.!?;:()\[\]\'" ]'
    )

    # ---------- 3. 读取并修改 CSV ----------
    temp_file = latest_csv + '.tmp'
    deleted_count = 0

    with open(latest_csv, 'r', encoding='utf-8-sig', newline='') as infile, \
         open(temp_file, 'w', encoding='utf-8-sig', newline='') as outfile:

        reader = csv.reader(infile)
        writer = csv.writer(outfile)

        for row in reader:
            if row:
                cleaned_text = pattern.sub('', row[0])
                cleaned_text = keep_pattern.sub('', cleaned_text)
                cleaned_text = re.sub(r'[ \t]{2,}', ' ', cleaned_text).strip()
                chinese_count = len(re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf]', cleaned_text))
                if chinese_count >= 80:
                    writer.writerow([cleaned_text])
                else:
                    deleted_count += 1
            else:
                writer.writerow([])

    os.replace(temp_file, latest_csv)
    print(f"✅ 已删除所有中括号表情，并删除汉字不足80的行 {deleted_count} 条，文件已更新：{latest_csv}")


# ---------- 5. 不带参数的广告判断函数 ----------

def is_advertisement():
    current_title = get_processing_title()
    if not current_title:
        raise ValueError("show_times.json 中没有 '正在处理中' 的电视剧")

    json_file = f'{current_title}.json'

    with open(json_file, 'r', encoding='utf-8') as _f:
        _data = json.load(_f)
    _done = [x["id"] for x in _data if x.get("status") == "已抓取"]
    if not _done:
        raise FileNotFoundError(f"{json_file} 中没有已处理的 id，请先运行抓取脚本")

    latest_csv = f'{_done[-1]}.csv'
    if not os.path.exists(latest_csv):
        raise FileNotFoundError(f"没有找到 CSV 文件：{latest_csv}，请先运行抓取脚本")
    print(f"正在处理文件：{latest_csv}")

    temp_file = latest_csv + '.tmp'
    deleted_count = 0

    replace_map = {
        'huanbi': '浣碧',
        '视频': '内容',
    }

    with open(latest_csv, 'r', encoding='utf-8-sig', newline='') as infile, \
         open(temp_file, 'w', encoding='utf-8-sig', newline='') as outfile:

        reader = csv.reader(infile)
        writer = csv.writer(outfile)

        for row in reader:
            if not row:
                writer.writerow([])
                continue

            text = row[0].strip()
            if not text:
                writer.writerow(row)
                continue

            t = text.lower()
            score = 0

            # 基于统计特征识别无意义夸赞/刷屏式评论
            cn_chars = re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf]', text)
            cn_count = len(cn_chars)
            if cn_count >= 80:
                four_char_segments = re.findall(r'[\u4e00-\u9fff]{4}', text)
                four_char_count = len(four_char_segments)
                four_char_ratio = four_char_count / (cn_count / 4.0)

                excl_count = text.count('！') + text.count('!')

                unique_cn = len(set(cn_chars))
                repeat_ratio = 1 - unique_cn / cn_count

                has_specific = bool(re.search(r'[0-9a-zA-Z@#]', text))

                long_non_cn = re.search(r'[^\u4e00-\u9fff]{8,}', text)

                if (four_char_ratio > 0.45 and
                    excl_count >= 5 and
                    repeat_ratio > 0.4 and
                    not has_specific):
                    deleted_count += 1
                    continue

                if (long_non_cn and four_char_count >= 8 and excl_count >= 5):
                    deleted_count += 1
                    continue

            if re.search(r'#[^#\s]{1,50}#', t):
                score += 2
            if re.search(r'@[\w\u4e00-\u9fa5-]{1,30}', t):
                score += 1
            if re.search(r'《[^》]{1,40}》', t):
                score += 2

            platform_words = [
                '爱奇艺', '腾讯视频', '优酷', '芒果tv', '哔哩哔哩',
                '央视', '卫视', '剧场', '自制', '独播', '出品'
            ]
            for w in platform_words:
                if w in t:
                    score += 1

            action_words = [
                '上线', '开播', '定档', '开机', '杀青', '首播', '播出',
                '预约', '追剧', '锁定', '关注', '转发', '期待', '更新',
                '会员', '大结局', '收官'
            ]
            for w in action_words:
                if w in t:
                    score += 1

            marketing_words = [
                '重磅', '热血', '悬案', '全新', '传奇', '蓄势待发', '敬请期待',
                '引爆', '炸裂', '必看', '王炸', '阵容', '高能', '反转', '虐恋',
                '甜宠', '权谋', '仙侠', '古装', '都市', '悬疑', '刑侦', '年代',
                '谍战', '科幻', '奇幻', '喜剧', '家庭', '职场', '青春', '爱情'
            ]
            for w in marketing_words:
                if w in t:
                    score += 1

            drama_words = [
                '电视剧', '剧集', '网剧', '连续剧', '短剧', '微短剧', '新剧',
                '大剧', '主演', '领衔主演', '导演', '编剧', '制片人', '角色',
                '饰演', '剧情', '预告', '片花', '花絮', '海报', '组讯',
                '第二季', '番外'
            ]
            for w in drama_words:
                if w in t:
                    score += 1

            if (re.search(r'\d{1,2}月\d{1,2}日', t) or
                re.search(r'\d{1,2}月[上中下]旬', t) or
                re.search(r'\d{1,2}[./-]\d{1,2}', t)):
                score += 1

            if score >= 4:
                deleted_count += 1
                continue

            new_text = row[0]
            for k, v in replace_map.items():
                new_text = new_text.replace(k, v)

            writer.writerow([new_text])

    os.replace(temp_file, latest_csv)
    print(f"✅ 已删除广告行 {deleted_count} 条，文件已更新：{latest_csv}")


if __name__ == '__main__':
    filter_comment()
    is_advertisement()