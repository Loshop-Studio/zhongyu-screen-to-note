#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
核心逻辑：把截图推到云笔记，覆盖同一本。

第一次点 → 新建笔记，把 fileId（笔记身份）和 page_hash（页资源路径）存到本地。
之后每次点 → 复用这两个值，只换掉一张 screenshot.png。

⚠️ 跨天必须换 page_hash
   云端路径是 note_v2/res/{userId}/{日期}/{fileId}/{page_hash}/...，
   而临时凭证按天签发，**只允许写当天日期的目录**。
   过了午夜还往昨天的目录写会直接 403 AccessDenied。
   换一个 page_hash 就等于在新的一天里开一份新资源，笔记本身不变。
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import zy_client
import zy_upload


def build_client():
    """按本程序自己的账号配置建客户端并登录。"""
    acct = config.load_account()
    if not acct:
        raise RuntimeError("还没设置账号")
    cli = zy_client.ZhongYuClient(
        acct.get("apiBase") or config.DEFAULT_API_BASE,
        acct.get("userName", ""), acct.get("password", ""))
    cli.login()
    return cli


def _note_exists(cli, file_id):
    """
    查这本笔记还在不在云端。

    ⚠️ 用户在平板上把笔记删掉后，本地还记着旧 ID，而服务端数据库里的记录
    可能还没清干净 —— 再拿旧 ID 去 AddOrUpdate 就会撞主键：
        Duplicate entry 'sxzxx-690-xxx' for key 'ezy_notes.PRIMARY'
    所以每次推送前都确认一下。

    查询本身失败时（网络抖动等）一律当作"还在"，宁可按老路走，
    也不要因为一次网络问题就莫名其妙新建一本笔记。
    """
    try:
        return any(n.get("fileId") == file_id for n in cli.get_all_notes())
    except Exception:
        return True


def push(image_path, log=None):
    """
    把一张图推上去。有笔记就覆盖，没有就新建。

    log —— 进度回调（给什么就调什么，传 None 就静默）。

    返回 (结果 dict, 是不是新建的)
    """
    say = log if log is not None else (lambda *_a, **_k: None)

    say("登录中…")
    cli = build_client()
    st = config.load_note_state()
    today = datetime.date.today().strftime("%Y%m%d")

    # 笔记可能被用户在平板上删了 —— 先确认它还在不在云端
    if st and not _note_exists(cli, st["fileId"]):
        say("这本笔记已经不在云端了（应该是被删了），重新建一本")
        config.clear_note_state()
        st = None

    if st:
        # 同一本笔记（fileId 不变），page_hash 决定页资源在 OSS 上的路径。
        # 同一天内一直复用 → 路径不变，只换一张图，最省流量。
        # 跨天则必须换新的 page_hash → 落到今天的目录，否则 403。
        same_day = (st.get("originDay") == today)
        if same_day:
            page_hash = st["pageHash"]
            say("覆盖已有笔记：%s" % st["fileId"])
        else:
            page_hash = str(int(datetime.datetime.now().timestamp() * 1000))
            say("已跨天（%s → %s），换到今天的资源目录"
                % (st.get("originDay"), today))
            say("覆盖已有笔记：%s" % st["fileId"])

        try:
            r = zy_upload.upload_note_page(
                cli, image_path, note_name=st.get("noteName"),
                keep_source=True, progress=say,
                reuse_file_id=st["fileId"],
                reuse_page_hash=page_hash)
        except zy_client.ZhongYuError as e:
            # 兜底：上面那次检查万一没看出来（接口没返回这本、或刚好网络抖动），
            # 服务端建笔记时会说主键重复。那就忘掉旧记录，换个新 ID 重来一次。
            if "Duplicate entry" in str(e):
                say("云端还留着旧记录，换个新笔记重建")
                config.clear_note_state()
                return push(image_path, log)
            raise

        if not same_day:
            # 只有跨天才写回；今天之内的后续推送就都复用这条路径了
            config.save_note_state(st["fileId"], page_hash, today,
                                   r.get("fileName") or st.get("noteName"))
        return r, False

    # 首次：先建一本，把身份记下来
    say("还没有笔记，新建一本")
    r = zy_upload.upload_note_page(cli, image_path, progress=say,
                                   note_name="电脑显示", keep_source=True)
    page_hash = _guess_page_hash(r)
    config.save_note_state(r["fileId"], page_hash, today, r["fileName"])
    return r, True


def _guess_page_hash(result):
    """从返回的 URL 里抠出那 13 位毫秒时间戳（也就是 page_hash）。"""
    import re
    url = result.get("screenshot") or result.get("fileUrl") or ""
    m = re.search(r"/(\d{13})/", url)
    if m:
        return m.group(1)
    return str(int(datetime.datetime.now().timestamp() * 1000))
