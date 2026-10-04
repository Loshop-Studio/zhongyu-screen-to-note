#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云笔记客户端（读取部分）。

几个要点：

  1. HTTP 走标准库 urllib，并【显式禁用代理】。
     urllib 默认会读取 HTTP_PROXY / HTTPS_PROXY 环境变量，把请求交给代理；
     明明能直连的服务器会因此返回 502，看着像"必须先挂代理"。这里用
     ProxyHandler({}) 强制直连，不吃环境变量的亏。

  2. 资源列表返回【完整原始字段】。
     保留 md5 / updateTimeStamp / pageIndex 等，供上层判断"内容有没有变"，
     而不只是数页数。

  3. 密钥按【当天日期】动态生成，并带跨天自动重建。
     常驻进程跨过午夜后密钥会失效，所以要按天重建。

本模块只做读取，不发起任何写入。
"""

import argparse
import base64
import datetime
import json
import os
import sys
import urllib.error
import urllib.request

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

DEFAULT_API_BASE = "https://sxzxx-api.zyai.cc"

# 强制直连：显式禁用一切代理（http/https/ftp...）
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class ZhongYuError(RuntimeError):
    """本模块统一抛出的业务异常。"""


def aes_key(now=None) -> bytes:
    """
    按当天日期动态生成 16 位密钥。

    构成：head(1) + 按日期旋转后取 14 位 + 当日字符(1) = 16 位
    """
    e = ":F0wKU!Qg3}UkbW+w[:9|D3-5h=:T;7t#_GZ4#G;~ZNSq{8;}QIP>'{q.lje"
    t = now or datetime.datetime.now()
    y, m, d = t.year, t.month, t.day
    c = (y * m * d) % len(e)
    head = chr((33 + d * m * 33) % 94 + 33)
    tail = e[d + m]
    return (head + (e[c:] + e[:c])[:14] + tail).encode()


class ZhongYuClient:
    """云笔记只读客户端。"""

    def __init__(self, api_base: str = DEFAULT_API_BASE,
                 user_name: str = "", password: str = ""):
        self.api_base = (api_base or DEFAULT_API_BASE).rstrip("/")
        self.user_name = user_name
        self.password = password
        self.token = None
        self.real_name = ""
        self._cipher_day = None
        self._cipher = None

    # ------------------------------------------------------------------ 加解密

    def _get_cipher(self):
        """当天的 AES-ECB cipher，跨天自动重建。"""
        today = datetime.date.today()
        if self._cipher_day != today or self._cipher is None:
            self._cipher_day = today
            self._cipher = AES.new(aes_key(), AES.MODE_ECB)
        return self._cipher

    def enc(self, s: str) -> str:
        ct = self._get_cipher().encrypt(pad(s.encode("utf-8"), 16))
        return base64.b64encode(ct).decode()

    def dec(self, s: str) -> str:
        pt = self._get_cipher().decrypt(base64.b64decode(s))
        return unpad(pt, 16).decode("utf-8")

    # ------------------------------------------------------------------ HTTP

    def _request(self, url, method="GET", body=None, headers=None, timeout=30):
        h = {"User-Agent": _UA, "Accept": "application/json, text/plain, */*"}
        if headers:
            h.update(headers)
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h["Content-Type"] = "application/json; charset=UTF-8"
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            with _DIRECT_OPENER.open(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except urllib.error.URLError as e:
            raise ZhongYuError("网络不可达（已强制直连、未走代理）：%s\n%s"
                               % (url, e.reason)) from e

    def _auth_headers(self):
        return {"Authorization": "Bearer " + (self.token or "")}

    def _authed_get(self, url, timeout=60):
        """带 401 自动重登一次的 GET。"""
        status, text = self._request(url, headers=self._auth_headers(), timeout=timeout)
        if status == 401:
            self.login()
            status, text = self._request(url, headers=self._auth_headers(), timeout=timeout)
        if status != 200:
            raise ZhongYuError("请求失败 HTTP %s：%s" % (status, text[:200]))
        return text

    # ------------------------------------------------------------------ 业务

    def login(self):
        status, text = self._request(
            self.api_base + "/api/TokenAuth/Login",
            method="POST",
            body={"userName": self.user_name,
                  "password": self.password,
                  "clientType": 1},
            timeout=30,
        )
        try:
            data = json.loads(text)
        except Exception:
            raise ZhongYuError("登录返回不是 JSON（HTTP %s）：%s" % (status, text[:200]))
        if not data.get("result"):
            err = data.get("error") or {}
            raise ZhongYuError("登录失败：" + (err.get("message") or text[:200]))
        self.token = data["result"]["accessToken"]
        self.real_name = (
            data["result"].get("user", {}).get("name")
            or data["result"].get("realName") or ""
        )
        return data["result"]

    def whoami(self):
        """取一次真实姓名（可选，失败不影响主流程）。"""
        try:
            text = self._authed_get(
                self.api_base + "/api/services/app/User/GetInfoAsync", timeout=30)
            self.real_name = (json.loads(text).get("result") or {}).get("realName", "")
        except Exception:
            pass
        return self.real_name

    def get_all_notes(self):
        """全部云笔记。返回 noteList（原始字段）。"""
        text = self._authed_get(
            self.api_base + "/CloudNotes/api/Notes/GetAll", timeout=90)
        raw = json.loads(text).get("data")
        if raw is None:
            raise ZhongYuError("GetAll 未返回 data：" + text[:200])
        inner = json.loads(self.dec(raw))
        return inner.get("noteList", []) or []

    def get_resources(self, file_id: str):
        """
        某本笔记的【完整】资源列表。

        这里刻意不做任何字段裁剪——md5 / updateTimeStamp / resourceType 全留着，
        上层要靠它们判断"这页有没有被改过"，而不只是"页数有没有变"。
        """
        q = self.enc("fileId=" + file_id)
        text = self._authed_get(
            self.api_base + "/CloudNotes/api/Resources/GetByFileId?" + q, timeout=60)
        raw = json.loads(text).get("data")
        if raw is None:
            raise ZhongYuError("GetByFileId 未返回 data：" + text[:200])
        inner = json.loads(self.dec(raw))
        return inner.get("resourceList", []) or []


def build_client(creds: dict = None, do_login: bool = True) -> ZhongYuClient:
    """
    建客户端（默认顺带登录）。

    creds 不传时，读本程序自己的账号配置
    （%LOCALAPPDATA%\\ScreenPush\\account.json，界面第一次运行时填写的）。
    """
    if not creds:
        import config                       # 惰性导入，避免循环引用
        creds = config.load_account()
    if not creds:
        raise ZhongYuError("还没有账号信息，请先运行一次程序填写登录信息")
    cli = ZhongYuClient(creds.get("apiBase", DEFAULT_API_BASE),
                        creds.get("userName", ""), creds.get("password", ""))
    if do_login:
        cli.login()
    return cli


def notes_of_interest(notes):
    """只保留云笔记本体（type 1/12），过滤掉文件夹等。"""
    return [n for n in notes if n.get("type") in (1, 12)]


_BAD_CHARS = '\\/:*?"<>|\r\n\t'


def safe_name(s) -> str:
    """把笔记名净化为合法的文件夹名（Windows 保留字符全部替换）。"""
    s = "".join("_" if c in _BAD_CHARS else c for c in str(s or "")).strip()
    s = s.rstrip(". ") or "未命名"
    return s[:80]


# ====================================================================== 自测


def _selftest():
    """离线自测：验证密钥长度、AES 往返、跨天重建。不需要账号和网络。"""
    k = aes_key()
    assert len(k) == 16, "密钥长度应为 16，实际 %d" % len(k)

    cli = ZhongYuClient()
    s = "笔记fileId=abc123&页数=3"
    assert cli.dec(cli.enc(s)) == s, "AES 往返失败"

    # 跨天：人为把 cipher 的日期改旧，下一次取用应触发重建
    cli._cipher_day = datetime.date(2000, 1, 1)
    cli._get_cipher()
    assert cli._cipher_day == datetime.date.today(), "跨天重建失败"

    # 不同日期密钥应当不同
    a = aes_key(datetime.datetime(2026, 9, 30))
    b = aes_key(datetime.datetime(2026, 10, 1))
    assert a != b, "不同日期密钥不应相同"

    # 同一日期密钥应当稳定
    assert aes_key(datetime.datetime(2026, 9, 30)) == a, "同日密钥应稳定"

    print("[自测通过] 密钥长度 16 / AES 往返正常 / 跨天自动重建 / 同日密钥稳定")
    print("          今天(%s)的密钥前 4 字节: %r" % (datetime.date.today(), k[:4]))


def _main():
    ap = argparse.ArgumentParser(description="云笔记只读客户端")
    ap.add_argument("--selftest", action="store_true", help="离线自测，不需要账号")
    ap.add_argument("--whoami", action="store_true", help="登录并打印身份")
    ap.add_argument("--notes", action="store_true", help="列出全部云笔记")
    ap.add_argument("--resources", metavar="FILE_ID", help="打印某本笔记的完整资源字段")
    args = ap.parse_args()

    if args.selftest:
        _selftest()
        return

    cli = build_client()
    print("已登录：%s（%s）" % (cli.whoami() or "未知", cli.api_base))

    if args.whoami:
        return

    if args.notes or args.resources:
        notes = notes_of_interest(cli.get_all_notes())
        print("共 %d 本云笔记" % len(notes))
        for i, n in enumerate(notes, 1):
            print("  %3d. %s  fileId=%s  type=%s  create=%s"
                  % (i, n.get("fileName"), n.get("fileId"),
                     n.get("type"), n.get("createTime")))

    if args.resources:
        res = cli.get_resources(args.resources)
        print("\n资源条数：%d" % len(res))
        if res:
            print("单条资源的全部字段（用于确认 md5 / 时间戳是否存在）：")
            print(json.dumps(res[0], ensure_ascii=False, indent=2))
            keys = set()
            for r in res:
                keys |= set(r.keys())
            print("\n所有条目出现过的字段全集：")
            print("  " + ", ".join(sorted(keys)))


if __name__ == "__main__":
    try:
        _main()
    except ZhongYuError as e:
        print("[出错] " + str(e))
        sys.exit(1)
