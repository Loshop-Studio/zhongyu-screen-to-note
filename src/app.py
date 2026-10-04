#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
屏幕推送 —— 入口。

用法：
  双击运行        没有账号就弹登录框，有就直接出悬浮窗
  再次双击运行    检测到已经在跑，就把悬浮窗重新叫出来（不会重复开）
  --cli           命令行模式（调试用）
"""
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MUTEX_NAME = "Global\\ScreenPush_Running_%s" % os.environ.get("USERNAME", "u")


def _log_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "ScreenPush")
    if not os.path.isdir(d):
        os.makedirs(d)
    return os.path.join(d, "error.log")


def _log_error():
    """--windowed 模式下异常是看不见的，写文件免得排查时抓瞎。"""
    try:
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write("\n=== %s ===\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            traceback.print_exc(file=f)
    except Exception:
        pass



def already_running():
    """
    靠一个命名互斥量判断是不是已经在跑。
    用 ctypes 而不是装 pywin32 —— 少一个依赖，打出来的 exe 也小一圈。
    """
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = wintypes.HANDLE
    h = k32.CreateMutexW(None, False, MUTEX_NAME)
    already = (k32.GetLastError() == 183)   # ERROR_ALREADY_EXISTS
    if not already:
        # 句柄留着不关，进程活着就一直占着
        globals()["_MUTEX_HANDLE"] = h
    return already


def main():
    if "--cli" in sys.argv:
        import config
        import capture
        import core
        if not config.has_account():
            print("尚未设置账号，请先双击运行一次。")
            return
        img = capture.grab_page(scale=config.IMG_SCALE,
                                quality=config.JPEG_QUALITY)[0]
        try:
            r, isnew = core.push(img)
            print("新建" if isnew else "覆盖", "->", r["screenshot"])
        finally:
            if os.path.exists(img):
                os.remove(img)
        return

    already = already_running()

    import tkinter as tk
    import config
    import ui

    if not config.has_account():
        if not ui.ask_account():
            sys.exit(0)
        if already:
            return

    if already:
        # 已经在跑了：把它的悬浮窗叫到前面来，然后自己退出
        _focus_existing()
        return

    bar = ui.FloatingBar()
    bar._place()
    bar.root.deiconify()
    bar.run()


def _focus_existing():
    """把已在运行的悬浮窗提到最前。"""
    try:
        import ctypes
        hwnd = ctypes.windll.user32.FindWindowW(None, "屏幕推送")
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 5)      # SW_SHOW
            ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _log_error()
        raise
