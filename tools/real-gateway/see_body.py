#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
see_body.py —— 看 pi 发给网关的**完整请求 body**。

做法：在 pi 和真网关之间插一个本地转发代理。
  1. 读 ~/.pi/agent 的 models.json / settings.json，记住真网关地址
  2. 造一个临时 pi 配置目录：models.json 的 baseUrl 改成 http://127.0.0.1:<端口>，
     其余（auth.json、settings.json）原样复制 —— 你真的 ~/.pi/agent 一个字不动
  3. 起一个转发代理：收到请求 -> 完整记下 body -> 原样转发给真网关 -> 把响应原样送回
  4. 用 PI_CODING_AGENT_DIR 指到那个临时目录，跑一次 pi
  5. 打印每个请求的完整 body（Authorization 打码）+ 响应

用法（在 WSL 里）：
    python3 ~/pinew/tools/real-gateway/see_body.py
    python3 ~/pinew/tools/real-gateway/see_body.py -p "写一个 hello.py"
    python3 ~/pinew/tools/real-gateway/see_body.py --keep      # 保留临时目录和原始记录

注意：这会**真的发请求**，按你的 models.json 里的价格计费。跑完临时目录会删掉。
"""

import argparse
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

REDACT = ("authorization", "x-api-key", "api-key", "cookie", "proxy-authorization")


# ───────────────────────────── 配置目录 ─────────────────────────────
def agent_dir():
    return os.environ.get("PI_CODING_AGENT_DIR") or os.path.expanduser("~/.pi/agent")


def read_json(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def pick_model(models, settings, model_arg):
    """-> (provider 名, 模型 id)"""
    providers = models.get("providers") or {}
    if model_arg:
        name, _, mid = model_arg.partition("/")
    else:
        name, mid = settings.get("defaultProvider"), settings.get("defaultModel")
    name = name or next(iter(providers), None)
    p = providers.get(name) or {}
    mid = mid or (p.get("models") or [{}])[0].get("id")
    if not name or not mid:
        sys.exit("models.json 里找不到 provider / 模型。用 --model provider/模型id 指定。")
    if not any(x.get("id") == mid for x in p.get("models") or []):
        sys.exit("models.json 里找不到 %s/%s" % (name, mid))
    return name, mid


def base_url_of(models, provider):
    u = (models.get("providers") or {}).get(provider, {}).get("baseUrl") or ""
    return u.rstrip("/")


def upstream_url(base, path):
    """base 可能自带路径前缀（https://host/v1）。别把 /v1 拼两遍。"""
    base = base.rstrip("/")
    bpath = urlsplit(base).path
    if bpath and bpath != "/" and path.startswith(bpath):
        path = path[len(bpath):] or "/"
    return base + path


# ───────────────────────────── 记录代理 ─────────────────────────────
class Recorder:
    def __init__(self, base, out_path):
        self.base = base
        self.out_path = out_path
        self.records = []
        self.lock = threading.Lock()

    def write(self, rec):
        with self.lock:
            self.records.append(rec)
        # 落盘时把敏感头打码（转发用的是内存里的原值）
        safe = dict(rec)
        if "headers" in safe:
            safe["headers"] = {k: ("<已打码>" if k.lower() in REDACT else v) for k, v in safe["headers"].items()}
        with open(self.out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(safe, ensure_ascii=False) + "\n")


def make_handler(recorder):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _forward(self, method, body):
            url = upstream_url(recorder.base, self.path)
            headers = {}
            for k, v in self.headers.items():
                if k.lower() in ("host", "content-length", "connection", "accept-encoding", "content-encoding"):
                    continue
                headers[k] = v
            headers["Accept-Encoding"] = "identity"
            req = urllib.request.Request(url, data=body if body else None, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    data = r.read()
                    return r.status, dict(r.headers), data, None
            except urllib.error.HTTPError as e:
                return e.code, dict(e.headers or {}), e.read(), None
            except Exception as e:
                return 599, {}, b"", "%s: %s" % (type(e).__name__, e)

        def _reply(self, status, headers, data):
            self.send_response(status)
            for k, v in headers.items():
                if k.lower() in ("transfer-encoding", "content-length", "connection", "content-encoding"):
                    continue
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except Exception:
                pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            hdrs = {k: v for k, v in self.headers.items()}
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except Exception:
                parsed = None
            status, rhdrs, data, err = self._forward("POST", raw)
            recorder.write({
                "kind": "request",
                "path": self.path,
                "headers": hdrs,
                "body": parsed if parsed is not None else raw.decode("utf-8", "replace"),
                "status": status,
                "error": err,
            })
            self._reply(status, rhdrs, data)

        def do_GET(self):
            status, rhdrs, data, err = self._forward("GET", None)
            recorder.write({"kind": "get", "path": self.path, "status": status, "error": err})
            self._reply(status, rhdrs, data)

    return Handler


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ───────────────────────────── 打印 ─────────────────────────────
def show_headers(h):
    out = {}
    for k, v in (h or {}).items():
        out[k] = ("<%d 字符，已打码>" % len(v)) if k.lower() in REDACT else v
    return out


def summarize(body):
    if not isinstance(body, dict):
        return {}
    s = {}
    s["顶层字段"] = sorted(body.keys())
    if "model" in body:
        s["model"] = body["model"]
    if "max_tokens" in body:
        s["max_tokens"] = body["max_tokens"]
    if "thinking" in body:
        s["thinking"] = body["thinking"]
    if "system" in body:
        sysv = body["system"]
        if isinstance(sysv, str):
            s["system"] = "%d 字符（一个字符串）" % len(sysv)
        elif isinstance(sysv, list):
            s["system"] = "%d 块，合计 %d 字符" % (len(sysv), sum(len(x.get("text") or "") for x in sysv))
    if "tools" in body:
        tl = body["tools"]
        s["tools"] = "%d 个：%s" % (len(tl), ", ".join(t.get("name", "?") for t in tl))
        s["tools 字符数"] = len(json.dumps(tl, ensure_ascii=False))
    if "messages" in body:
        s["messages"] = "%d 条：%s" % (len(body["messages"]), ", ".join(m.get("role", "?") for m in body["messages"]))
    return s


def dump(records, path):
    reqs = [r for r in records if r["kind"] == "request"]
    print("=" * 78)
    print("录到 %d 个请求；完整原始记录在 %s" % (len(reqs), path))
    print("=" * 78)
    for i, r in enumerate(reqs, 1):
        print("")
        print("#" * 78)
        print("### 请求 %d：POST %s   （上游返回 %s%s）" % (i, r["path"], r.get("status"), "，有错：" + r["error"] if r.get("error") else ""))
        print("#" * 78)
        print("--- 请求头（敏感的打码了）---")
        for k, v in show_headers(r["headers"]).items():
            print("  %-28s %s" % (k + ":", v))
        body = r["body"]
        if isinstance(body, dict):
            print("--- 摘要 ---")
            for k, v in summarize(body).items():
                print("  %-12s %s" % (k + ":", v))
            print("--- 完整 body ---")
            print(json.dumps(body, ensure_ascii=False, indent=2))
        else:
            print("--- 完整 body（非 JSON，原样）---")
            print(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--prompt", default="Reply with the single word: OK", help="给 pi 的消息（默认一句 OK）")
    ap.add_argument("--model", help="provider/模型id；默认用 settings.json 里的默认模型")
    ap.add_argument("--keep", action="store_true", help="保留临时目录和原始记录")
    ap.add_argument("--out", help="原始记录（JSONL）写到哪里")
    args = ap.parse_args()

    src = agent_dir()
    models = read_json(os.path.join(src, "models.json"))
    settings = read_json(os.path.join(src, "settings.json")) if os.path.exists(os.path.join(src, "settings.json")) else {}
    provider, model = pick_model(models, settings, args.model)
    base = base_url_of(models, provider)
    if not base:
        sys.exit("models.json 里 provider %s 没有 baseUrl，没法插代理。" % provider)
    print("模型 %s / %s   真网关 %s" % (provider, model, base))

    root = tempfile.mkdtemp(prefix="see-body-")
    priv = os.path.join(root, "agent")
    os.makedirs(priv, mode=0o700)

    port = free_port()
    # 代理的 baseUrl 保留真网关的路径前缀（如 /v1），这样 pi 算出来的请求路径和平时一模一样
    bpath = urlsplit(base).path or ""
    proxy_base = "http://127.0.0.1:%d%s" % (port, bpath if bpath != "/" else "")

    # models.json：只把选中的 provider 的 baseUrl 指到代理
    m2 = json.loads(json.dumps(models))
    m2["providers"][provider]["baseUrl"] = proxy_base
    with open(os.path.join(priv, "models.json"), "w", encoding="utf-8") as f:
        json.dump(m2, f, ensure_ascii=False, indent=1)
    # settings.json、auth.json 原样复制
    for fn in ("settings.json", "auth.json"):
        s = os.path.join(src, fn)
        if os.path.exists(s):
            shutil.copyfile(s, os.path.join(priv, fn))
            os.chmod(os.path.join(priv, fn), 0o600)

    out_path = args.out or os.path.join(root, "requests.jsonl")
    recorder = Recorder(base, out_path)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), make_handler(recorder))
    srv.daemon_threads = True
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    print("代理已起：%s -> %s" % (proxy_base, base))
    print("临时配置目录：%s" % priv)

    env = dict(os.environ, PI_CODING_AGENT_DIR=priv)
    pi_bin = "pi"
    if shutil.which("pi") is None:
        for c in ("~/.local/bin/pi", "~/node_modules/.bin/pi", "/usr/local/bin/pi", "~/.bun/bin/pi"):
            c = os.path.expanduser(c)
            if os.path.exists(c) and os.access(c, os.X_OK):
                pi_bin = c
                break
        else:
            sys.exit("找不到 pi。先确认 'pi --version' 能跑。")
    cmd = [pi_bin, "-p", args.prompt, "--mode", "json", "--no-session"]
    print("跑：%s\n" % " ".join(cmd))
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300)
        tail = (r.stdout or "").splitlines()
        print("pi 跑完了，退出码 %s。最后几行输出：" % r.returncode)
        for ln in tail[-6:]:
            print("   " + ln[:160])
        if r.stderr:
            print("pi 的 stderr（最后 5 行）：")
            for ln in r.stderr.splitlines()[-5:]:
                print("   " + ln[:160])
    except subprocess.TimeoutExpired:
        print("pi 超时了。")
    finally:
        time.sleep(0.5)
        srv.shutdown()

    print("")
    dump(recorder.records, out_path)

    if args.keep:
        print("\n临时目录留在：%s" % root)
    else:
        shutil.rmtree(root, ignore_errors=True)
        print("\n临时目录已删掉。")


if __name__ == "__main__":
    main()
