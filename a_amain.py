import os
import sys
import time
import random
import socket
import asyncio
import shutil
import json
from datetime import datetime, timedelta

from b_get_name import get_iqiyi_titles
from c_save_tv_name import save_title
from d_filter_videos import filter_videos
from e_crawl_video_id import crawl_video_id
import a_article_pic as ap
from f_crawl_comment import crawl_comment
from g_data_cleaning import filter_comment, is_advertisement
from h_cut_file import split_by_chinese
from i_intelligence import generate_article, resume_unfinished
from j_releasing import is_publish_paused, reset_publish_paused

from z_browser_config import get_profile_dir, get_headless
from invisible_playwright.async_api import InvisiblePlaywright

#///启动时的网络请求：有技术分析指出，该浏览器每次启动时会联系一次 GitHub，这一行为在大多数安装包中未被明确说明，需要留意。/根据技术分析，InvisiblePlaywright 捆绑的定制版 Firefox 浏览器，每次启动时确实会向 GitHub 发送一次请求。
#///目的：这通常用于检查版本更新或进行匿名使用统计（遥测）。作者在项目中也对此行为进行了披露，并提供了关闭的方法。
#///争议点：问题在于，大多数用户通过 pip install 安装的包里，并没有明确包含这个行为的说明，这导致了信息不透明，引发了像你一样的疑虑

# ========== 手动唤醒开关 ==========
MANUAL_RESUME = False
# ★ 手动唤醒时，让启动门禁跳过时间判断
_MANUAL_WAKE = False

# ========== ★ 每日唤醒时间（改这一行，全局统一） ==========
WAKE_TIME = "03:00"   # 格式 HH:MM

_WAKE_HOUR, _WAKE_MINUTE = map(int, WAKE_TIME.split(":"))

# ================== 单实例锁 ==================
def _check_single_instance(port=52123):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(('127.0.0.1', port))
        sock.listen(1)
        return sock
    except OSError:
        print("⚠️️ 检测到程序已经在运行中（端口被占用），本次不启动。")
        print("   如果要重新启动，请先关闭已有的 Python 进程。")
        sys.exit(0)


_single_instance_sock = None


# ================== 自动把 MANUAL_RESUME 改回 False ==================
def _reset_manual_resume_flag():
    try:
        with open(__file__, 'r', encoding='utf-8') as f:
            content = f.read()
        if 'MANUAL_RESUME = True' in content:
            content = content.replace('MANUAL_RESUME = True',
                                      'MANUAL_RESUME = False', 1)
            with open(__file__, 'w', encoding='utf-8') as f:
                f.write(content)
            print("🔧 已自动把 MANUAL_RESUME 改回 False。")
    except Exception as e:
        print(f"[警告] 自动重置 MANUAL_RESUME 失败（不影响运行）：{e}")


# ================== 智能挂起 ==================
def wait_for_resume():
    print("\n" + "=" * 50)
    print(f"🛑 程序已进入挂起状态，等待每天 {WAKE_TIME} 自动唤醒...")
    print("💡 想立刻继续：关闭程序，把 main.py 里的 MANUAL_RESUME 改成 True，再启动。")
    print("=" * 50)

    now = datetime.now()
    target = now.replace(hour=_WAKE_HOUR, minute=_WAKE_MINUTE, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    print(f"⏰ 下一次自动唤醒时间：{target.strftime('%Y-%m-%d %H:%M:%S')}")

    last_time_check = time.time()

    while True:
        time.sleep(30)
        if time.time() - last_time_check >= 1800:
            last_time_check = time.time()
            now = datetime.now()
            if now >= target:
                print(f"\n🌅 已到达 {WAKE_TIME}（{now.strftime('%Y-%m-%d %H:%M:%S')}），自动解除暂停，继续执行！")
                reset_publish_paused()
                return


# ================== ★ 启动门禁 ==================
PAUSE_STATE_FILE = 'pause_state.json'


def _is_today_paused():
    """今天是否已发完 / 平台额度已满。只看 pause_state.json。"""
    if not os.path.exists(PAUSE_STATE_FILE):
        return False
    try:
        with open(PAUSE_STATE_FILE, 'r', encoding='utf-8') as f:
            state = json.load(f)
    except Exception:
        return False
    today = datetime.now().strftime('%Y-%m-%d')
    return bool(state.get('paused')) and state.get('pause_date') == today


async def _wait_until_allowed_start():
    """启动门禁：
    - 手动唤醒        -> 直接放行
    - 未到 WAKE_TIME        -> 等今天 WAKE_TIME
    - >= WAKE_TIME 且今天已暂停 -> 等明天 WAKE_TIME
    - >= WAKE_TIME 且今天未暂停 -> 直接放行
    """
    # ★ 手动唤醒优先，跳过一切时间判断
    if _MANUAL_WAKE:
        print("[启动门禁] 检测到手动唤醒，跳过时间判断，直接运行。")
        return

    while True:
        now = datetime.now()
        today_wake = now.replace(hour=_WAKE_HOUR, minute=_WAKE_MINUTE, second=0, microsecond=0)

        if now < today_wake:
            target = today_wake
            reason = f"未到每日唤醒时间 {WAKE_TIME}"
        else:
            if _is_today_paused():
                target = today_wake + timedelta(days=1)
                reason = "今天已发完/平台额度已满"
            else:
                if os.path.exists(PAUSE_STATE_FILE):
                    reset_publish_paused()
                print("[启动门禁] 当前可运行。")
                return

        print(f"[启动门禁] {reason}，挂起到 {target.strftime('%Y-%m-%d %H:%M:%S')}")
        while datetime.now() < target:
            await asyncio.sleep(10)


# ================== 清理 Firefox 会话残留（只删不建）==================
def _clean_firefox_session(profile_dir):
    if not os.path.isdir(profile_dir):
        print(f"[会话清理] {os.path.basename(profile_dir)}：目录不存在，跳过")
        return

    deleted = []

    backups_dir = os.path.join(profile_dir, 'sessionstore-backups')
    if os.path.isdir(backups_dir):
        try:
            shutil.rmtree(backups_dir)
            deleted.append('sessionstore-backups/')
        except Exception as e:
            print(f"[会话清理] 删除 sessionstore-backups 失败：{e}")

    for fname in ['sessionstore.jsonlz4', 'sessionstore.js', 'sessionstore.bak']:
        fpath = os.path.join(profile_dir, fname)
        if os.path.isfile(fpath):
            try:
                os.remove(fpath)
                deleted.append(fname)
            except Exception as e:
                print(f"[会话清理] 删除 {fname} 失败：{e}")

    if deleted:
        print(f"[会话清理] {os.path.basename(profile_dir)}：已删除 {', '.join(deleted)}")
    else:
        print(f"[会话清理] {os.path.basename(profile_dir)}：干净，无需清理")


def _clean_all_profiles():
    """清理所有会用到的 profile。"""
    print("\n" + "=" * 50)
    print("[会话清理] 正在清理各 profile 的 Firefox 会话残留...")
    print("=" * 50)
    for mode in ['douyin', 'baijiahao']:
        _clean_firefox_session(get_profile_dir(mode))


# ================== 主逻辑（async）==================
async def main_async():

    # ★ 启动门禁：先把挂起规则判断掉，再决定跑不跑
    await _wait_until_allowed_start()

    # ------------------流程：爱奇艺 → 保存剧名 → 抖音筛选 → 抓ID → 抓图
    # # 1) 爱奇艺找剧名
    # print('=== 第一步：爱奇艺找剧名 ===')
    # titles = await get_iqiyi_titles()
    #
    # # 2) 保存电视剧名称
    # print('\n=== 第二步：保存电视剧名称 ===')
    # save_title(titles)

    # # 3) 抖音筛选ID和保存ID（返回 pw, browser, page, title）
    # print('\n=== 第三步：抖音筛选剧名 ===')
    # pw, browser, page, title = await filter_videos()
    # if pw is None:
    #     print("抖音筛选未返回结果，跳过后续抓取，直接进入评论循环。")
    # else:
    #     try:
    #         print('\n=== 第四步：获取所有视频 id ===')
    #         video_ids = await crawl_video_id(page, show_title=title)
    #         print(f"完成，共 {len(video_ids)} 个视频 id")
    #     finally:
    #         try:
    #             await pw.__aexit__(None, None, None)
    #             print("[浏览器] 抖音筛选用浏览器已关闭。")
    #         except Exception:
    #             pass
    #
    #     await asyncio.sleep(1.5)
    #

        # # 4) 抓取图片
        # print('\n=== 第五步：抓取所有图片，并保存 ===')
        # await ap.scrape_and_open_links()

    # ------------------流程：爱奇艺 → 保存剧名 → 抖音筛选 → 抓ID → 抓图



    # 【第六步】会话清理 + 评论抓取循环/启动浏览器之前，先把所有 profile 的会话残留清掉
    _clean_all_profiles()

    try:
        while True:
            # 0. 已经暂停 → 挂起
            if is_publish_paused():
                await asyncio.to_thread(wait_for_resume)
                continue

            # 1. 检查有没有上次没发完的视频
            resume_result = await resume_unfinished()

            if resume_result == 'DAILY_LIMIT':
                print("今日平台额度已用完，进入挂起等待...")
                await asyncio.to_thread(wait_for_resume)
                continue

            if resume_result == 'PAUSED':
                await asyncio.to_thread(wait_for_resume)
                continue

            if resume_result:
                continue

            # ======================================================
            # 2. 抓取视频评论
            # ★ 每次进入这里都会换一个新的抖音账号 profile（a/c 轮换）
            # ======================================================
            print('\n=== 抓取视频评论 ===')
            print("[浏览器] 为本次评论抓取分配新的抖音账号 profile...")
            cmt_profile = get_profile_dir('douyin_cmt')

            cmt_pw = InvisiblePlaywright(seed=42, profile_dir=cmt_profile, headless=get_headless())
            try:
                cmt_browser = await cmt_pw.__aenter__()
            except BaseException as e:
                print(f"❌ 评论抓取浏览器启动失败：{e}")
                await asyncio.sleep(random.uniform(10, 20))
                continue

            force_video_id = None
            restart_count = 0
            result = None

            try:
                while True:
                    ret = await crawl_comment(
                        cmt_browser,
                        force_video_id=force_video_id,
                        restart_count=restart_count,
                    )

                    if isinstance(ret, tuple) and len(ret) == 3 and ret[0] == "NEED_RESTART":
                        _, vid, new_rc = ret
                        print(f"🔄 连续失败，重启浏览器（{new_rc}/2），继续抓 id={vid}")

                        try:
                            await cmt_pw.__aexit__(None, None, None)
                        except Exception:
                            pass
                        await asyncio.sleep(random.uniform(2, 4))

                        cmt_pw = InvisiblePlaywright(seed=42, profile_dir=cmt_profile, headless=get_headless())
                        try:
                            cmt_browser = await cmt_pw.__aenter__()
                        except BaseException as e:
                            print(f"❌ 重启浏览器失败：{e}")
                            result = "FAILED"
                            break

                        force_video_id = vid
                        restart_count = new_rc
                        continue

                    result = ret
                    break
            finally:
                try:
                    await cmt_pw.__aexit__(None, None, None)
                    print("[浏览器] 评论抓取浏览器已关闭（下一个视频会换账号）。")
                except Exception:
                    pass

            if result == "NO_MORE":
                print("所有视频已处理完毕，退出循环。")
                break
            elif result == "FAILED":
                print("本次抓取失败，已回滚，跳过继续下一个。")
                await asyncio.sleep(random.uniform(3, 10))
                continue

            # 3. 清洗第一个视频数据
            print('\n=== 清洗第一个视频数据 ===')
            filter_comment()
            is_advertisement()

            # 4. 切割文件
            print('\n=== 切割文件 ===')
            if not split_by_chinese(max_chars=1000, min_chars=600):
                continue

            # 5. 生成并逐篇发布
            print('\n=== 生成并逐篇发布 ===')
            gen_result = await generate_article()

            if gen_result == 'DAILY_LIMIT':
                print("今日平台额度已用完，进入挂起等待...")
                await asyncio.to_thread(wait_for_resume)
                continue

            if gen_result == 'PAUSED':
                await asyncio.to_thread(wait_for_resume)
                continue

    except KeyboardInterrupt:
        print("\n检测到手动中断。")
        raise


# ================== 同步入口 ==================
def main():
    global MANUAL_RESUME, _MANUAL_WAKE

    if MANUAL_RESUME:
        print("🔧 检测到 MANUAL_RESUME = True，启动时自动解除暂停，继续执行。")
        reset_publish_paused()
        MANUAL_RESUME = False
        _MANUAL_WAKE = True
        _reset_manual_resume_flag()

    asyncio.run(main_async())


if __name__ == '__main__':
    _single_instance_sock = _check_single_instance()

    try:
        main()
    except KeyboardInterrupt:
        print("\n检测到手动中断，程序退出。")
    finally:
        if _single_instance_sock:
            try:
                _single_instance_sock.close()
            except Exception:
                pass