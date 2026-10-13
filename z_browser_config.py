import os

BASE = os.path.dirname(os.path.abspath(__file__))


# ========== ★ 全局无头开关 ==========
# True  = 全部浏览器都无头（速度快，反检测弱）
# False = 全部浏览器都有头（速度慢，反检测强）
HEADLESS = False
# ========== 各站点专属 profile 目录 ==========
PROFILES = {
    'sina':       os.path.join(BASE, 'profile_sina'),           # 新浪新闻抓图
    'iqiyi':      os.path.join(BASE, 'profile_iqiyi'),          # 爱奇艺找剧名
    'baijiahao':  os.path.join(BASE, 'profile_baijiahao'),      # 百家号发布（固定，绝不轮换）
}


# ========== ★ 抖音账号（A / C），所有抖音场景统一用这两个 ==========
_DOUYIN_A = os.path.join(BASE, 'profile_douyin_a')
_DOUYIN_C = os.path.join(BASE, 'profile_douyin_c')

# 抖音筛选 / 抓ID 用
DOUYIN_PROFILES = [_DOUYIN_A, _DOUYIN_C]

# 抖音评论抓取用
DOUYIN_CMT_PROFILES = [_DOUYIN_A, _DOUYIN_C]


# 索引持久化文件
_INDEX_FILE     = os.path.join(BASE, 'user_data_index.txt')
_CMT_INDEX_FILE = os.path.join(BASE, 'douyin_cmt_index.txt')


def _load_index(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except Exception:
        return 0


def _save_index(path, i):
    try:
        with open(path, 'w') as f:
            f.write(str(i))
    except Exception:
        pass


_user_data_index  = _load_index(_INDEX_FILE)
_douyin_cmt_index = _load_index(_CMT_INDEX_FILE)


def get_profile_dir(mode='douyin', user_data_path=None):
    """返回本次要用的 profile 目录。

    - user_data_path 指定了：强制用它，不改 index
    - mode='baijiahao' ：固定 PROFILES['baijiahao']（绝不轮换）
    - mode='douyin'    ：在 DOUYIN_PROFILES 里轮换（筛选/抓ID）
    - mode='douyin_cmt'：在 DOUYIN_CMT_PROFILES 里轮换（评论抓取，每个视频换一次）
    - 其他 mode        ：直接返回对应固定目录，不轮换
    """
    global _user_data_index, _douyin_cmt_index

    if user_data_path is not None:
        print(f"[浏览器] 本次手动指定 profile：{user_data_path}")
        return user_data_path

    # ---------- 百家号：固定，绝不轮换 ----------
    if mode == 'baijiahao':
        path = PROFILES['baijiahao']
        print(f"[浏览器] 百家号模式，固定 profile：{path}")
        return path

    # ---------- 抖音评论抓取：轮换 ----------
    if mode == 'douyin_cmt':
        idx = _douyin_cmt_index % len(DOUYIN_CMT_PROFILES)
        path = DOUYIN_CMT_PROFILES[idx]
        print(f"[浏览器] 抖音评论抓取（第 {idx + 1}/{len(DOUYIN_CMT_PROFILES)} 个账号）：{path}")
        _douyin_cmt_index += 1
        _save_index(_CMT_INDEX_FILE, _douyin_cmt_index)
        return path

    # ---------- 抖音筛选/抓ID：轮换 ----------
    if mode == 'douyin':
        idx = _user_data_index % len(DOUYIN_PROFILES)
        path = DOUYIN_PROFILES[idx]
        print(f"[浏览器] 抖音多账号模式（第 {idx + 1}/{len(DOUYIN_PROFILES)} 个）：{path}")
        _user_data_index += 1
        _save_index(_INDEX_FILE, _user_data_index)
        return path

    # ---------- 其他 mode：直接用同名固定目录 ----------
    path = PROFILES.get(mode)
    if path is None:
        path = os.path.join(BASE, f'profile_{mode}')
        print(f"[浏览器] mode='{mode}' → 独立 profile：{path}")
    return path


# ========== ★ 全局无头开关的对外接口 ==========
def get_headless():
    """返回当前是否无头。改顶部 HEADLESS 变量即可一键切换所有浏览器。"""
    return HEADLESS

