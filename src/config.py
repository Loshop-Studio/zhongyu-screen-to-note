#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置与状态：账号凭据 + 笔记身份（fileId / page_hash / 原始日期）。

两样都存在**使用者自己电脑上**，路径固定在
    %LOCALAPPDATA%\\ScreenPush\\
所以把这个程序分享给别人时，你的账号不会跟着走。
"""
import json
import os

APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                       "ScreenPush")
CREDS_FILE = os.path.join(APP_DIR, "account.json")
STATE_FILE = os.path.join(APP_DIR, "note.json")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
LOG_FILE = os.path.join(APP_DIR, "push.log")

DEFAULT_API_BASE = "https://sxzxx-api.zyai.cc"
DEFAULT_SCHOOL = "sxzxx"


def api_base_for_school(school):
    """
    学校代码 -> 服务器地址。sxzxx 就对应 sxzxx-api.zyai.cc。

    这样别的学校的人只要填自己的代码就能用，不用改代码。
    代码填得不认识时退回默认地址（至少本校能用）。
    """
    s = (school or "").strip().lower()
    if not s:
        return DEFAULT_API_BASE
    import re
    if not re.match(r"^[a-z0-9_-]{2,32}$", s):
        return DEFAULT_API_BASE
    return "https://%s-api.zyai.cc" % s

# 笔记页尺寸不再写死 —— 由 capture.page_size_for() 按屏幕比例动态算，
# 这样图能铺满整页、不留白（2026-10-04 实测 App 接受非标准页面尺寸）。
IMG_SCALE = 1.0        # 图在页面里的占比（1.0 = 铺满）
JPEG_QUALITY = 85

# 推送间隔（秒）—— 两个模式各一份设置
# ⚠️ 下限 4 秒 —— 一次截图+上传大概 2~3 秒，比这更快只会让请求互相排队，
#    平板上反而更卡，流量白烧。
MIN_INTERVAL = 4.0
DEFAULT_AUTO_INTERVAL = 5.0     # 自动：无条件每隔这么久推一张
DEFAULT_SMART_INTERVAL = 5.0    # 智能：切应用后的最高刷新率（最快多久刷一次）


def _ensure_dir():
    if not os.path.isdir(APP_DIR):
        os.makedirs(APP_DIR)


def save_account(user_name, password, school=DEFAULT_SCHOOL,
                 api_base=DEFAULT_API_BASE):
    _ensure_dir()
    data = {"userName": user_name, "password": password,
            "schoolCode": school, "apiBase": api_base}
    with open(CREDS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def load_account():
    if not os.path.exists(CREDS_FILE):
        return None
    try:
        with open(CREDS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    if not d.get("userName") or not d.get("password"):
        return None
    d.setdefault("apiBase", DEFAULT_API_BASE)
    d.setdefault("schoolCode", DEFAULT_SCHOOL)
    return d


def has_account():
    return load_account() is not None


# ---------------------------------------------------------------- 一般设置

def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_settings(d):
    _ensure_dir()
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return d


def _clean_interval(v, default):
    """间隔必须是 >= MIN_INTERVAL 的数字，不合规就退回默认值。"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    if v < MIN_INTERVAL:
        return default
    return v


def get_auto_interval():
    """自动模式的推送间隔（秒）。"""
    return _clean_interval(load_settings().get("autoInterval"),
                           DEFAULT_AUTO_INTERVAL)


def get_smart_interval():
    """智能模式的最高刷新率（秒）—— 切应用后最快多久刷一次。"""
    return _clean_interval(load_settings().get("smartInterval"),
                           DEFAULT_SMART_INTERVAL)


def set_interval(key, seconds):
    d = load_settings()
    d[key] = float(seconds)
    save_settings(d)
    return d[key]


def fmt_seconds(v):
    """4.0 -> '4'，4.5 -> '4.5'，显示用。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f == int(f) else ("%.1f" % f)


# ---------------------------------------------------------------- 上传日志

def get_show_log():
    """
    是否记录上传日志（默认开）。

    为什么默认开：打包成 exe 后是 --windowed 模式，**根本没有控制台**，
    日志只能落文件。出问题时这是唯一的线索，所以默认就留着。
    """
    v = load_settings().get("showLog", True)
    return bool(v)


def set_show_log(on):
    d = load_settings()
    d["showLog"] = bool(on)
    save_settings(d)
    return bool(on)


class PushLogger(object):
    """
    一次推送的日志收集器。

    · 卸载/打包后都能用：写 `%LOCALAPPDATA%\\ScreenPush\\push.log`
    · 源码模式（有黑窗口）时顺带 print 出来，实时看
    · 每次推送**覆盖写**，文件不会越长越大，里面永远是最近一次的过程
    """

    MAX_BYTES = 512 * 1024

    def __init__(self, enabled, path=None, echo=True, title=None):
        self.enabled = bool(enabled)
        self.path = path or LOG_FILE
        self.echo = echo
        self._fh = None
        self._buf = []
        if self.enabled:
            self._open(title)

    def _open(self, title):
        try:
            _ensure_dir()
            import datetime
            self._fh = open(self.path, "w", encoding="utf-8")
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self("=== 屏幕推送 %s %s ===" % (stamp, title or ""))
        except Exception:
            self._fh = None

    def __call__(self, msg):
        if not self.enabled:
            return
        line = str(msg)
        self._buf.append(line)
        if self._fh:
            try:
                self._fh.write(line + "\n")
                self._fh.flush()
            except Exception:
                pass
        # exe 的 --windowed 模式下 sys.stdout 是 None，print 会炸，所以裹住
        if self.echo:
            try:
                print(line, flush=True)
            except Exception:
                pass

    def close(self):
        if self._fh:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None
        # 兜底：万一文件写废了，至少别超过上限
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) > self.MAX_BYTES:
                with open(self.path, "r+", encoding="utf-8") as f:
                    f.truncate(self.MAX_BYTES)
        except Exception:
            pass


def save_note_state(file_id, page_hash, origin_day, note_name):
    _ensure_dir()
    data = {"fileId": file_id, "pageHash": page_hash,
            "originDay": origin_day, "noteName": note_name}
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def load_note_state():
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    if not d.get("fileId") or not d.get("pageHash"):
        return None
    return d


def clear_note_state():
    """
    忘掉本地记的那本笔记。

    什么时候用：用户在平板上把这本笔记删了。
    此时云端列表里已经查不到它，但服务端数据库里的记录还没清干净，
    继续拿旧 ID 去 AddOrUpdate 会撞主键（Duplicate entry）。
    把它忘掉、换一个新 ID 重建，就绕开了。
    """
    try:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
        return True
    except Exception:
        return False
