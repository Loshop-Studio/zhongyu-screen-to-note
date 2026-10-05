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

        r = zy_upload.upload_note_page(
            cli, image_path, note_name=st.get("noteName"),
            keep_source=True, progress=say,
            reuse_file_id=st["fileId"],
            reuse_page_hash=page_hash)

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
