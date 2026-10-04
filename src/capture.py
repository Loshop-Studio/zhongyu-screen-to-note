#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
截图 + 排版成笔记页。

要点（都是踩过的坑）：
  · 必须先声明 DPI 感知 —— 本机缩放 165%，不声明坐标全错
  · 抓**工作区**而不是全屏 —— 任务栏会自动排除
  · JPEG q85 —— 屏幕截图大片纯色，PNG 要 2MB，JPEG 只要 150KB
  · 缩放用 **contain**（宽高哪个更紧按哪个），只按宽度缩会在个别比例上裁掉内容
  · 页面尺寸**跟着屏幕比例走**，这样图铺满整页、下方不留白
    （2026-10-04 实测：App 接受 1920×823 / 1080 / 1440 等非标准页面尺寸）
"""
import ctypes
import os
import tempfile

from PIL import Image
from PIL import ImageGrab

# 笔记页：宽度固定 1920（已验证可用），高度按屏幕比例算
PAGE_W = 1920
# 高度安全范围 —— 实测 823~1440 都能正常显示，两端再留点余量。
# 超出范围的极端比例会被钳住（代价是有点留白，换来的是稳定）。
PAGE_H_MIN, PAGE_H_MAX = 720, 1800
# 兜底：算不出比例时用的传统竖版页
PAGE_H_FALLBACK = 2184


def page_size_for(img_w, img_h):
    """
    按屏幕宽高比算出笔记页尺寸：宽 1920，高 = 1920 / 比例。

    16:9 -> 1080    16:10 -> 1200    4:3 -> 1440    21:9 -> 823

    ⚠️ 竖屏（高 > 宽）不属于支持范围（见 README「已知限制」）；
    这里不做特殊处理，只保证比例异常时能退回兜底尺寸、不会算出荒谬的值。
    """
    try:
        if img_w <= 0 or img_h <= 0:
            return PAGE_W, PAGE_H_FALLBACK
        h = int(round(PAGE_W / (float(img_w) / float(img_h))))
    except Exception:
        return PAGE_W, PAGE_H_FALLBACK
    if h < PAGE_H_MIN or h > PAGE_H_MAX:
        return PAGE_W, PAGE_H_FALLBACK
    return PAGE_W, h


def _enable_dpi_awareness():
    """165% 缩放下不声明 DPI 感知，截图会跟实际窗口对不上。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def work_area():
    """主显示器工作区（去掉任务栏），物理像素。返回 (l, t, w, h)。"""
    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                    ("r", ctypes.c_long), ("b", ctypes.c_long)]

    rc = RECT()
    ok = ctypes.windll.user32.SystemParametersInfoW(0x0030, 0,
                                                     ctypes.byref(rc), 0)
    if not ok:
        raise RuntimeError("取不到工作区矩形")
    return rc.l, rc.t, rc.r - rc.l, rc.b - rc.t


def grab_page(page_w=None, page_h=None, scale=1.0, quality=85, align="center"):
    """
    截整屏 → 等比缩放到**能完整放进**笔记页 → 补白，返回临时文件路径。

    不传 page_w / page_h 时，页面尺寸按屏幕比例自动算（推荐）——
    这样图能**铺满整页**，不会有那半页白边。

    ⚠️ 缩放比例必须取 min(页宽/图宽, 页高/图高)（也就是 contain），
    不能只按页宽缩 —— 那样在高度大的屏幕上，图会超出页面被裁掉一截。

    scale  —— 在 contain 基础上再乘一个比例（1.0 = 顶满，0.9 = 留一圈边）
    align  —— "center" 居中（默认）/ "top" 贴顶

    返回 (临时文件路径, 屏幕工作区尺寸, 图尺寸, 页面尺寸)
    """
    _enable_dpi_awareness()
    l, t, ww, wh = work_area()

    img = ImageGrab.grab(bbox=(l, t, l + ww, t + wh), all_screens=True)

    if not page_w or not page_h:
        page_w, page_h = page_size_for(img.width, img.height)

    # contain：两个方向都塞得进去才不会被裁
    fit = min(page_w / float(img.width), page_h / float(img.height))
    fit *= max(0.1, min(1.0, scale))
    new_w = max(1, int(round(img.width * fit)))
    new_h = max(1, int(round(img.height * fit)))
    if (new_w, new_h) != img.size:
        img = img.resize((new_w, new_h), Image.LANCZOS)

    canvas = Image.new("RGB", (page_w, page_h), (255, 255, 255))
    x = (page_w - new_w) // 2
    y = 0 if align == "top" else (page_h - new_h) // 2
    canvas.paste(img, (x, y))

    fd, path = tempfile.mkstemp(prefix="screenpush_", suffix=".jpg")
    os.close(fd)
    canvas.save(path, "JPEG", quality=quality, optimize=True)
    return path, (ww, wh), (new_w, new_h), (page_w, page_h)
