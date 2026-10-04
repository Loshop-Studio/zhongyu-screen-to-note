#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
界面：首次登录框 + 常驻悬浮窗（两种模式）。

模式：
  · 手动：点一下推一次（安全、可控）
  · 自动：每 5 秒检查一次前台窗口，exe 变了就推

悬浮窗右键菜单可切换 / 退出。无标题栏，置顶，右下角。
"""
import os
import queue as _queue
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config


def enable_dpi_awareness():
    """
    ⚠️ 必须在 tk.Tk() **之前**调用 —— Tk 在创建窗口时就把屏幕尺寸和 DPI 缓存了，
    之后再声明 DPI 感知已经来不及（界面仍会被系统拉伸放大）。

    不声明的话，165% 缩放下截图坐标和窗口位置都会错位。
    """
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE
        return True
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            return True
        except Exception:
            return False


def apply_scaling(root):
    """
    DPI 感知之后再告诉 Tk「一个点等于多少像素」，点数字体才会跟着放大，
    不然设了感知但字体还是按 96 DPI 算，字会小得看不清。
    """
    import ctypes
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)   # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi <= 0:
            dpi = 96
        root.tk.call("tk", "scaling", dpi / 72.0)
        return dpi / 96.0
    except Exception:
        return 1.0


BG = "#F7F8FA"
CARD = "#FFFFFF"
FG = "#1F2328"
SUB = "#6B7280"
ACCENT = "#2563EB"
OK = "#059669"
ERR = "#DC2626"
WARN = "#D97706"

MODE_FILE = os.path.join(config.APP_DIR, "mode.txt")

# 三种模式
#   manual 手动 —— 点一下推一次
#   smart  智能 —— 每 5 秒查前台窗口，换了应用才推（省流量）
#   auto   自动 —— 每 5 秒无条件推一次（相当于实时直播）
MODES = ("manual", "smart", "auto")
MODE_LABEL = {
    "manual": "手动",
    "smart": "智能",
    "auto": "自动",
}
_MODE_HINT = {
    "manual": "点击推送",
    "smart": "换应用才推",
    "auto": "每5秒推送",
}
AUTO_INTERVAL_MS = 5000      # 自动模式默认间隔（会被 settings.json 覆盖）
SMART_CHECK_MS = 2000        # 智能模式查前台窗口的间隔（固定，只管"发现得及不及时"）
SMART_POLL_MS = 500          # 主线程检查"该不该推了"的节拍
MIN_INTERVAL = 4.0           # 间隔下限（秒）


def _read_mode():
    """读上次的模式，认不出来的（比如手改坏了）一律退回手动。"""
    if os.path.exists(MODE_FILE):
        try:
            with open(MODE_FILE, "r", encoding="utf-8") as f:
                m = f.read().strip()
            if m in MODES:
                return m
        except Exception:
            pass
    return "manual"


def _write_mode(mode):
    config._ensure_dir()
    with open(MODE_FILE, "w", encoding="utf-8") as f:
        f.write(mode)


def ask_account(parent=None):
    """首次打开：填账号密码，存在**使用者自己电脑**上。"""
    if not parent:
        enable_dpi_awareness()
    dlg = tk.Toplevel(parent) if parent else tk.Tk()
    if not parent:
        apply_scaling(dlg)
    dlg.title("屏幕推送 — 设置账号")
    dlg.configure(bg=BG)
    dlg.resizable(False, False)
    if parent:
        dlg.transient(parent)

    body = tk.Frame(dlg, bg=BG, padx=22, pady=18)
    body.pack(fill="both", expand=True)

    tk.Label(body, text="设置账号", bg=BG, fg=FG,
             font=tkfont.Font(size=14, weight="bold")).pack(anchor="w")
    tk.Label(body, text="三个都填上，密码只存在这台电脑上",
             bg=BG, fg=SUB, font=tkfont.Font(size=9)).pack(anchor="w", pady=(2, 14))

    def field(label, default="", show=None, hint=None):
        tk.Label(body, text=label, bg=BG, fg=FG,
                 font=tkfont.Font(size=9)).pack(anchor="w")
        e = tk.Entry(body, font=tkfont.Font(size=10), relief="solid", bd=1,
                     highlightthickness=0, show=show or "")
        e.pack(fill="x", pady=(2, 6 if hint else 10))
        if default:
            e.insert(0, default)
        if hint:
            tk.Label(body, text=hint, bg=BG, fg=SUB,
                     font=tkfont.Font(size=8)).pack(anchor="w", pady=(0, 10))
        return e

    # ⚠️ 三个框必须分开、且各自有标签 ——
    # 之前把学校代码预填进"用户名"框里，看着像只有一个学校输入框，纯属误导。
    e_school = field("学校代码", "sxzxx",
                     hint="决定连哪台服务器，通常就是学校拼音缩写")
    e_user = field("用户名")
    e_pw = field("密码", show="*")
    e_pw.focus_set()

    lbl = tk.Label(body, text="", bg=BG, fg=ERR, font=tkfont.Font(size=9))
    lbl.pack(anchor="w", pady=(2, 8))

    result = {"ok": False}
    msgs = _queue.Queue()          # 验证线程 → 主线程的消息通道

    def _safe_destroy():
        try:
            dlg.destroy()
        except Exception:
            pass

    def poll():
        """
        主线程节拍：取验证结果。
        ⚠️ 验证跑在子线程里，**不能让它直接 dlg.after / 改 lbl** ——
        那会报 "main thread is not in main loop"，症状是对话框永远停在
        "正在验证…" 既不提示也不关闭，看着像卡死。
        """
        try:
            while True:
                kind, payload = msgs.get_nowait()
                if kind == "ok":
                    lbl.config(text="验证通过", fg=OK)
                    result["ok"] = True
                    dlg.after(250, _safe_destroy)
                    return
                lbl.config(text="失败：" + payload, fg=ERR)
        except _queue.Empty:
            pass
        except Exception:
            pass
        try:
            dlg.after(150, poll)
        except Exception:
            pass

    def submit(_evt=None):
        school = e_school.get().strip().lower()
        u, p = e_user.get().strip(), e_pw.get().strip()
        if not school or not u or not p:
            lbl.config(text="学校代码、用户名、密码都要填", fg=ERR)
            return
        api = config.api_base_for_school(school)
        lbl.config(text="正在验证…", fg=SUB)
        dlg.update()

        def work():
            try:
                import zy_client
                cli = zy_client.ZhongYuClient(api, u, p)
                cli.login()
                config.save_account(u, p, school, api)
                msgs.put(("ok", None))
            except Exception as e:
                msgs.put(("err", str(e)[:90]))

        threading.Thread(target=work, daemon=True).start()

    e_pw.bind("<Return>", lambda e: submit())
    e_school.bind("<Return>", lambda e: e_user.focus_set())
    e_user.bind("<Return>", lambda e: e_pw.focus_set())
    dlg.after(150, poll)

    b = tk.Button(body, text="保存", command=submit, bg=ACCENT, fg="white",
                  activebackground="#1D4FD8", activeforeground="white",
                  relief="flat", bd=0, cursor="hand2",
                  font=tkfont.Font(size=10, weight="bold"), height=1)
    b.pack(fill="x", ipady=4)

    def on_close():
        dlg.destroy()

    dlg.protocol("WM_DELETE_WINDOW", on_close)
    dlg.bind("<Escape>", lambda e: on_close())

    if parent is None:
        dlg.mainloop()
        return result["ok"]
    dlg.wait_window()
    return result["ok"]


# --------------------------------------------------------- 间隔/刷新率设置

def ask_interval(parent, title, hint, default_value):
    """
    输入一个秒数。

    title / hint 由调用方给，**两个模式必须写得不一样** —— 不然用户分不清
    自己调的是"自动模式的推送间隔"还是"智能模式的最高刷新率"。

    返回 (是否确定, 新值)。
    """
    dlg = tk.Toplevel(parent)
    dlg.title(title)
    dlg.configure(bg=BG)
    dlg.resizable(False, False)
    dlg.transient(parent)
    try:
        dlg.grab_set()
    except Exception:
        pass

    body = tk.Frame(dlg, bg=BG, padx=22, pady=18)
    body.pack(fill="both", expand=True)

    tk.Label(body, text=title, bg=BG, fg=FG,
             font=tkfont.Font(size=13, weight="bold")).pack(anchor="w")

    # wraplength 是像素，要跟着 DPI 缩放走，否则 165% 下换行会难看
    try:
        scale = parent.winfo_fpixels("1i") / 96.0
    except Exception:
        scale = 1.0
    tk.Label(body, text=hint, bg=BG, fg=SUB, font=tkfont.Font(size=9),
             justify="left", wraplength=int(300 * scale)).pack(anchor="w",
                                                                pady=(5, 14))

    row = tk.Frame(body, bg=BG)
    row.pack(fill="x", pady=(0, 6))
    e = tk.Entry(row, width=8, font=tkfont.Font(size=12),
                 relief="solid", bd=1, highlightthickness=0)
    e.pack(side="left")
    e.insert(0, config.fmt_seconds(default_value))
    e.select_range(0, "end")
    tk.Label(row, text="秒", bg=BG, fg=SUB,
             font=tkfont.Font(size=10)).pack(side="left", padx=(6, 0))

    lbl = tk.Label(body, text="最小 %s 秒" % config.fmt_seconds(config.MIN_INTERVAL),
                   bg=BG, fg=SUB, font=tkfont.Font(size=9))
    lbl.pack(anchor="w", pady=(0, 12))

    result = {"ok": False, "value": None}

    def submit(_e=None):
        raw = e.get().strip()
        try:
            v = float(raw)
        except ValueError:
            lbl.config(text="请填数字", fg=ERR)
            return
        if v < config.MIN_INTERVAL:
            lbl.config(text="最小不能低于 %s 秒"
                            % config.fmt_seconds(config.MIN_INTERVAL), fg=ERR)
            return
        result["ok"] = True
        result["value"] = v
        dlg.destroy()

    e.bind("<Return>", submit)
    dlg.bind("<Escape>", lambda ev: dlg.destroy())

    btns = tk.Frame(body, bg=BG)
    btns.pack(fill="x")
    tk.Button(btns, text="确定", command=submit, bg=ACCENT, fg="white",
              activebackground="#1D4FD8", activeforeground="white",
              relief="flat", bd=0, cursor="hand2",
              font=tkfont.Font(size=10, weight="bold"),
              width=8).pack(side="right", ipady=3)
    tk.Button(btns, text="取消", command=dlg.destroy, bg="#E5E7EB", fg=FG,
              activebackground="#D1D5DB", relief="flat", bd=0, cursor="hand2",
              font=tkfont.Font(size=10),
              width=8).pack(side="right", padx=(0, 8), ipady=3)

    e.focus_set()
    dlg.wait_window()
    return result


# ----------------------------------------------------------------- 悬浮窗

class FloatingBar(object):

    def __init__(self):
        enable_dpi_awareness()          # 必须在 Tk() 之前
        self.root = tk.Tk()
        apply_scaling(self.root)
        self.root.title("屏幕推送")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#D5D9E0")
        self.root.resizable(False, False)

        wrap = tk.Frame(self.root, bg="#D5D9E0", padx=1, pady=1)
        wrap.pack()

        self.card = tk.Frame(wrap, bg=CARD, padx=14, pady=10)
        self.card.pack()

        self.lbl_title = tk.Label(self.card, text="屏幕推送", bg=CARD, fg=FG,
                                  font=tkfont.Font(size=10, weight="bold"))
        self.lbl_title.pack(anchor="w")

        self.lbl_state = tk.Label(self.card, text="就绪 · 点击推送", bg=CARD,
                                  fg=SUB, font=tkfont.Font(size=9))
        self.lbl_state.pack(anchor="w", pady=(3, 0))

        # 左键：按一下=推送；按住拖动=挪位置。右键=菜单
        self._drag = {"ox": 0, "oy": 0, "moved": False}
        for w in (self.card, self.lbl_title, self.lbl_state):
            w.bind("<Button-1>", self.on_press)
            w.bind("<B1-Motion>", self.on_drag)
            w.bind("<ButtonRelease-1>", self.on_release)
            w.bind("<Button-3>", self.on_context)
            w.configure(cursor="hand2")

        self.busy = False
        self.mode = _read_mode()               # manual / smart / auto
        self._mode_var = tk.StringVar(value=self.mode)   # 给右键菜单的单选钮用
        self.watcher = None                    # 智能模式：前台窗口监听
        self.auto_timer = None                 # 自动模式：定时器
        self.smart_timer = None                # 智能模式：主线程限速检查
        self._smart_pending = None             # 待推送的前台应用（子线程只写它）
        self._last_push_time = 0               # 上次推送时刻（智能模式限速用）
        self.auto_interval = config.get_auto_interval()
        self.smart_interval = config.get_smart_interval()
        self.show_log = config.get_show_log()
        self._log_var = tk.BooleanVar(value=self.show_log)
        self._ui_queue = _queue.Queue()        # 子线程 → 主线程的结果通道

        self._place()
        self._apply_mode()                     # 按上次的模式启动
        self.root.after(200, self._poll_ui)
        self.root.after(500, self._watch_state)

    def _place(self):
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry("%dx%d+%d+%d" % (w, h, sw - w - 30, sh - h - 60))

    def _update_text(self):
        if self.mode == "smart":
            self.lbl_state.config(
                text="智能 · 换应用才推（最快 %s 秒）"
                     % config.fmt_seconds(self.smart_interval), fg=WARN)
        elif self.mode == "auto":
            self.lbl_state.config(
                text="自动 · 每 %s 秒推送"
                     % config.fmt_seconds(self.auto_interval), fg=ACCENT)
        else:
            self.lbl_state.config(text="手动 · 点击推送", fg=SUB)

    def _apply_mode(self):
        """按当前 mode 起停后台任务（启动时和切换时都走这里）。"""
        self._stop_smart()
        self._stop_auto()
        if self.mode == "smart":
            self._start_smart()
        elif self.mode == "auto":
            self._start_auto()
        self._update_text()

    def _watch_state(self):
        try:
            import config as cfg
            p = os.path.join(cfg.APP_DIR, "last.txt")
            if os.path.exists(p) and not self.busy and self.mode == "manual":
                with open(p, "r", encoding="utf-8") as f:
                    txt = f.read().strip()
                    if txt:
                        self.lbl_state.config(text=txt, fg=SUB)
        except Exception:
            pass
        self.root.after(2000, self._watch_state)

    def on_press(self, evt):
        """记住按下时的偏移，用于区分'拖动'和'单击'。"""
        self._drag["ox"] = evt.x_root - self.root.winfo_x()
        self._drag["oy"] = evt.y_root - self.root.winfo_y()
        self._drag["moved"] = False

    def on_drag(self, evt):
        nx = evt.x_root - self._drag["ox"]
        ny = evt.y_root - self._drag["oy"]
        # 移动超过 4 像素才算拖动，避免手抖把点击吃掉
        if (abs(nx - self.root.winfo_x()) > 4
                or abs(ny - self.root.winfo_y()) > 4):
            self._drag["moved"] = True
        self.root.geometry("+%d+%d" % (nx, ny))

    def on_release(self, evt):
        """没拖动过就当成一次点击。"""
        if not self._drag["moved"]:
            self.on_click()

    def on_click(self, _evt=None):
        if self.busy:
            return
        self._push("手动触发")

    def on_context(self, evt=None):
        menu = tk.Menu(self.root, tearoff=0, bg=CARD, fg=FG,
                       activebackground="#E5E7EB", activeforeground=FG,
                       font=tkfont.Font(size=9))
        menu.add_command(label="推送模式", state="disabled")
        for m in MODES:
            menu.add_radiobutton(
                label="  %s（%s）" % (MODE_LABEL[m], _MODE_HINT[m]),
                value=m, variable=self._mode_var,
                command=lambda mm=m: self.set_mode(mm))
        menu.add_separator()
        # ⚠️ 两条设置分开写清楚：一个是"定时推送间隔"，一个是"最高刷新率"，
        #    名字和说明都不能含糊，否则用户会分不清自己调的是哪个。
        menu.add_command(
            label="自动：推送间隔 %s 秒…"
                  % config.fmt_seconds(self.auto_interval),
            command=self._set_auto_interval)
        menu.add_command(
            label="智能：最高刷新率 %s 秒…"
                  % config.fmt_seconds(self.smart_interval),
            command=self._set_smart_interval)
        menu.add_separator()
        menu.add_checkbutton(label="记录上传日志", variable=self._log_var,
                             command=self._toggle_log)
        menu.add_command(label="打开日志文件", command=self._open_log)
        menu.add_separator()
        menu.add_command(label="退出", command=self._exit)
        x = self.root.winfo_pointerx()
        y = self.root.winfo_pointery()
        menu.post(x, y)
        if evt:
            menu.bind("<FocusOut>", lambda e: menu.destroy())

    def set_mode(self, mode):
        if mode == self.mode:
            return
        self.mode = mode
        _write_mode(mode)
        if self._mode_var.get() != mode:
            self._mode_var.set(mode)
        self._apply_mode()

    # -------------------------------------------------------- 间隔设置

    def _set_auto_interval(self):
        r = ask_interval(
            self.root,
            title="自动模式 · 推送间隔",
            hint="自动模式会按这个间隔**一直定时推送**，不管你切不切窗口。\n\n"
                 "填得越小画面越实时，流量也越大。",
            default_value=self.auto_interval)
        if not r["ok"]:
            return
        self.auto_interval = r["value"]
        config.set_interval("autoInterval", r["value"])
        if self.mode == "auto":          # 正在跑就按新节奏重排
            self._stop_auto()
            self._start_auto()
        self._update_text()

    def _set_smart_interval(self):
        r = ask_interval(
            self.root,
            title="智能模式 · 最高刷新率",
            hint="这里设的是**最高刷新率**，不是定时推送。\n\n"
                 "智能模式只在你**切换应用**时才推；填 10 就表示切换后最快\n"
                 "10 秒刷新一次。一直不换应用的话，一张都不会推。",
            default_value=self.smart_interval)
        if not r["ok"]:
            return
        self.smart_interval = r["value"]
        config.set_interval("smartInterval", r["value"])
        self._update_text()

    # -------------------------------------------------------- 上传日志

    def _toggle_log(self):
        self.show_log = bool(self._log_var.get())
        config.set_show_log(self.show_log)

    def _open_log(self):
        """用记事本打开日志 —— exe 模式没有黑窗口，这是唯一能找到日志的入口。"""
        try:
            p = config.LOG_FILE
            if not os.path.exists(p):
                config._ensure_dir()
                with open(p, "w", encoding="utf-8") as f:
                    f.write("(日志是空的 —— 可能还没推送过，或者关掉了「记录上传日志」)\n")
            os.startfile(p)          # 仅 Windows
        except Exception:
            pass

    # -------------------------------------------- 智能模式：换应用才推

    def _start_smart(self):
        import watcher
        self._smart_pending = None
        self._last_push_time = 0
        # 检查间隔固定 2 秒 —— 它只决定"发现你得及时不及时"，
        # 真正推多快由 smart_interval（最高刷新率）说了算。
        self.watcher = watcher.ForegroundWatcher(
            self._on_app_changed, interval=SMART_CHECK_MS / 1000.0)
        self.watcher.start()
        self._poll_smart()

    def _stop_smart(self):
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
        if self.smart_timer:
            try:
                self.root.after_cancel(self.smart_timer)
            except Exception:
                pass
            self.smart_timer = None
        self._smart_pending = None

    def _on_app_changed(self, exe_name):
        """
        ⚠️ 这个回调跑在 watcher 子线程里，**绝对不能碰 tk 控件**
        （连 root.after 也不行 —— 会报 "main thread is not in main loop"）。
        所以这里只写一个变量，剩下的交给主线程。
        """
        self._smart_pending = exe_name

    def _poll_smart(self):
        """主线程节拍：攒着的变化 + 最高刷新率，决定这一拍推不推。"""
        if self.mode != "smart":
            self.smart_timer = None
            return
        name = self._smart_pending
        # 正在传上一张时先不动 pending，等忙完再推，免得那一次变化白丢
        if (name and not self.busy
                and (time.time() - self._last_push_time) >= self.smart_interval):
            self._smart_pending = None
            self._push("前台: " + name.replace(".exe", ""), is_auto=True)
        self.smart_timer = self.root.after(SMART_POLL_MS, self._poll_smart)

    # -------------------------------------------- 自动模式：无条件定时推

    def _start_auto(self):
        self._auto_tick()

    def _auto_tick(self):
        """用 after 递归调度，保证永远在主线程里跑。"""
        if self.mode != "auto":
            return
        if not self.busy:          # 上一张还没传完就跳过这轮，不排队堆积
            self._push("定时推送", is_auto=True)
        self.auto_timer = self.root.after(
            max(100, int(self.auto_interval * 1000)), self._auto_tick)

    def _stop_auto(self):
        if self.auto_timer:
            try:
                self.root.after_cancel(self.auto_timer)
            except Exception:
                pass
            self.auto_timer = None

    def _push(self, reason, is_auto=False):
        if self.busy:
            return
        self.busy = True
        self._last_push_time = time.time()
        self.lbl_state.config(text=reason + " · 上传中…", fg=ACCENT)
        self.lbl_title.config(fg=ACCENT)
        self.root.update()
        threading.Thread(target=lambda: self._work(reason, is_auto),
                         daemon=True).start()

    def _work(self, reason, is_auto=False):
        """
        上传工作线程。
        ⚠️ 结果**不能直接 self.root.after(...)** —— 那还是从子线程碰 tkinter。
        一律塞队列，由主线程的 _poll_ui 取出来处理。
        """
        img = None
        logger = config.PushLogger(self.show_log, title=reason)
        try:
            import capture
            import core
            img, area, size, page = capture.grab_page(
                scale=config.IMG_SCALE, quality=config.JPEG_QUALITY)
            logger("截图：工作区 %dx%d → 页面 %dx%d，图 %dx%d，%d 字节"
                   % (area[0], area[1], page[0], page[1],
                      size[0], size[1], os.path.getsize(img)))
            res, isnew = core.push(img, log=logger)
            msg = ("新建笔记 " if isnew else "已更新 ") + time.strftime("%H:%M:%S")
            logger("完成：" + msg)
            self._ui_queue.put(("done", msg, OK))
        except Exception as e:
            logger("失败：" + str(e))
            self._ui_queue.put(("done", "失败：" + str(e)[:60], ERR))
        finally:
            logger.close()
            if img and os.path.exists(img):
                try:
                    os.remove(img)
                except Exception:
                    pass

    def _poll_ui(self):
        """主线程：把子线程塞进来的结果落到界面上。"""
        try:
            while True:
                item = self._ui_queue.get_nowait()
                if item and item[0] == "done":
                    self._done(item[1], item[2])
        except _queue.Empty:
            pass
        except Exception:
            pass
        self.root.after(200, self._poll_ui)

    def _done(self, msg, color):
        self.lbl_state.config(text=msg, fg=color)
        self.lbl_title.config(fg=FG)
        self.busy = False
        try:
            import config as cfg
            with open(os.path.join(cfg.APP_DIR, "last.txt"), "w",
                      encoding="utf-8") as f:
                f.write(msg)
        except Exception:
            pass

    def _exit(self):
        self._stop_smart()
        self._stop_auto()
        self.root.destroy()

    def run(self):
        self.root.mainloop()
