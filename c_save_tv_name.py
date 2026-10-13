import json
import os

FILE_PATH = 'tv_name.json'


def save_title(titles):
    print(f'开始处理这 {len(titles)} 个标题：')

    local_data = []
    if os.path.exists(FILE_PATH):
        try:
            with open(FILE_PATH, 'r', encoding='utf-8') as f:
                local_data = json.load(f)
            if not isinstance(local_data, list):
                local_data = []
        except (json.JSONDecodeError, OSError):
            local_data = []

    local_titles = {
        item.get('title') for item in local_data if isinstance(item, dict)
    }

    new_titles = []
    seen = set()
    for title in titles:
        if title in local_titles or title in seen:
            continue
        seen.add(title)
        new_titles.append(title)

    if not new_titles:
        print('没有新标题，无需保存。')
        return

    print(f'发现 {len(new_titles)} 个新标题：{new_titles}')

    new_data = [{'title': t, 'release_date': '未知'} for t in new_titles]
    results_data = new_data + local_data

    with open(FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(results_data, f, ensure_ascii=False, indent=4)

    print(f'已保存 {len(new_titles)} 个新标题至 {FILE_PATH}！')