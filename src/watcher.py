#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
前台窗口变化监听（自动模式用）。

只关心"当前最顶层窗口的 exe 变了"这件事，简单轮询就行。
"""
import ctypes
import ctypes.wintypes as w
import os
import threading
import time


def _enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# Windows API 签名
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = w.HWND
user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
user32.GetWindowThreadProcessId.restype = w.DWORD
kernel32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
kernel32.OpenProcess.restype = w.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD,
                                                 w.LPWSTR, ctypes.POINTER(w.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = w.BOOL

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class ForegroundWatcher(object):
    """
    后台线程：每 interval 秒查一次前台窗口，exe 变了就调 on_change。
    """

    def __init__(self, on_change, interval=5.0):
        self.on_change = on_change
        self.interval = interval
        self._running = False
        self._t = None
        self._last = None

    def _foreground_exe(self):
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = w.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                  False, pid.value)
        if not h:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = w.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
            return None
        finally:
            kernel32.CloseHandle(h)

    def _loop(self):
        while self._running:
            cur = self._foreground_exe()
            if cur and cur != self._last:
                self._last = cur
                try:
                    self.on_change(cur)
                except Exception:
                    import traceback
                    traceback.print_exc()
            # 拆成 0.5 秒一查，退出响应更快
            waited = 0.0
            while self._running and waited < self.interval:
                time.sleep(0.5)
                waited += 0.5

    def start(self):
        _enable_dpi_awareness()
        self._running = True
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def stop(self):
        self._running = False
        if self._t:
            self._t.join(timeout=2.0)

    def reset(self):
        """切回手动再切自动时，忽略当前窗口，等下一次变化。"""
        self._last = self._foreground_exe()
