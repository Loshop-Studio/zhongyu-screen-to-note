# -*- coding: utf-8 -*-
"""打包成单文件 exe。用 3.14（自带 Pillow + 已装 pycryptodome）。"""
import os, sys, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
TPL = os.path.join(HERE, "templates")
PY = sys.executable

cmd = [
    PY, "-m", "PyInstaller",
    "--noconfirm",
    "--onefile",
    "--windowed",
    "--name", "屏幕推送",
    "--distpath", os.path.join(HERE, "dist"),
    "--workpath", os.path.join(HERE, "build"),
    "--specpath", HERE,
    "--add-data", "%s;templates" % TPL,
    "--hidden-import", "Crypto.Cipher.AES",
    "--hidden-import", "watcher",
    "--exclude-module", "tkinter.test",
    os.path.join(SRC, "app.py"),
]
sys.exit(subprocess.run(cmd).returncode)
