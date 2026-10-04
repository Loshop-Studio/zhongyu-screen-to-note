#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
核心逻辑：把截图推到云笔记，覆盖同一本。

第一次点 → 新建笔记，把 fileId / page_hash / 原始日期 存到本地。
之后每次点 → 复用那三个值，只换掉一张 screenshot.png。
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import zy_client
import zy_upload


def _log(msg):
    print(msg, flush=True)


def build_client():
    """按本程序自己的账号配置建客户端并登录（不读官方那个工具的凭据）。"""
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
           界面上"显示日志"开关关掉时，这里会收到一个空函数。

    返回 (结果 dict, 是不是新建的)
    """
    say = log if log is not None else (lambda *_a, **_k: None)

    say("登录中…")
    cli = build_client()
    st = config.load_note_state()

    if st:
        # 覆盖模式：钉死 fileId + page_hash + 建笔记那天的日期
        zy_upload._ORIGINAL_DAY = st["originDay"]
        say("覆盖已有笔记：%s" % st["fileId"])
        r = zy_upload.upload_note_page(
            cli, image_path, note_name=st.get("noteName"),
            keep_source=True, progress=say,
            reuse_file_id=st["fileId"],
            reuse_page_hash=st["pageHash"])
        return r, False

    # 首次：先建一本，把身份记下来
    say("还没有笔记，新建一本")
    r = zy_upload.upload_note_page(cli, image_path, progress=say,
                                   note_name="电脑显示", keep_source=True)
    # 从 URL 里把 page_hash 和建笔记那天的日期抠出来存下
    page_hash = _guess_page_hash(r)
    origin_day = _guess_origin_day(r)
    config.save_note_state(r["fileId"], page_hash, origin_day, r["fileName"])
    return r, True


def _guess_page_hash(result):
    import re
    url = result.get("screenshot") or result.get("fileUrl") or ""
    m = re.search(r"/(\d{13})/", url)
    if m:
        return m.group(1)
    return str(int(datetime.datetime.now().timestamp() * 1000))


def _guess_origin_day(result):
    """
    从 fileUrl 里抠出 note_v2/res/{userId}/{originDay}/{fileId}/ 那段日期。
    这才是真正的"建笔记那天"，不能硬编码。
    """
    import re
    url = result.get("fileUrl") or ""
    m = re.search(r"res/\d+/(\d{8})/", url)
    if m:
        return m.group(1)
    return zy_upload._ORIGINAL_DAY
