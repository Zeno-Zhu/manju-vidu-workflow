# -*- coding: utf-8 -*-
"""
Vidu 渠道 · CLI / Token 直连模式（★ 默认通道）

为什么不用官方 vidu-cli：
  官方 CLI 的 `--schedule-mode` 只声明 `claw_pass | normal`，
  **在参数解析层就拒绝 `nopeak`**，拿不到「闲时创作 0 积分」。
  所以这里直接打底层 API。

协议（2026-10-20 实测）
  鉴权      Authorization: Token <VIDU_TOKEN>
  闲时额度  GET  /vidu/v1/tasks/quota  → nopeak_quota{used,total}   ★ 独立账本，不是积分
  积分      GET  /credit/v1/credits/me
  进行中    GET  /vidu/v1/tasks/history/me?pager.pagesz=100&pager.page=0
  提交      POST /vidu/v1/tasks
  单任务    GET  /vidu/v1/tasks/{id}
  上传      POST /tools/v1/files/uploads → PUT {put_url} → PUT /…/{id}/finish

硬约束
  · 闲时 200 条/天/账号；同时进行中最多 100 条 ⇒ 必须"掉一个补一个"
  · schedule_mode 只有 normal / nopeak（猜 idle/off_peak 一律 400）
  · 成品在 creations 数组；无水印取 download_uri

用法
  py scripts/vidu_cli.py quota
  py scripts/vidu_cli.py run   --jobs <项目>/jobs_vidu.json --all --mode nopeak
  py scripts/vidu_cli.py raw   --prompt-file 我的提示词.txt --image a.png --image b.png
  py scripts/vidu_cli.py pull  --jobs <项目>/jobs_vidu.json [--out <目录>]
  py scripts/vidu_cli.py accounts
  py scripts/vidu_cli.py image --prompt "…" --model pro --out <目录>            # 文生图 t2i
  py scripts/vidu_cli.py image --prompt "…" --ref 基准图.png --out <目录>       # 参考图生图 r2i
  ★ 生图默认 --mode normal（6 积分/张）；nopeak 仅 t2i 可用，r2i 传 nopeak 平台直接 403
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from license import ensure_activated  # noqa: E402

DEFAULT_BASE = "https://service.vidu.cn"
INFLIGHT = ("created", "preparation", "processing")
DONE = ("success", "failed", "error")

# 生图模型别名（与 vidu_image.py 一致）
IMG_MODELS = {"fast": "3.2_image_2_5_fast", "pro": "3.2_image_2_5_pro", "base": "3.2_image_2"}


# ─────────────────────── .env ───────────────────────
def load_dotenv():
    d = os.path.dirname(HERE)
    for _ in range(6):
        p = os.path.join(d, ".env")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return p
        nd = os.path.dirname(d)
        if d == nd:
            break
        d = nd
    return None


load_dotenv()


def base_url():
    return os.environ.get("VIDU_BASE_URL", DEFAULT_BASE).rstrip("/")


def tokens():
    out = {}
    if os.environ.get("VIDU_TOKEN"):
        out[0] = os.environ["VIDU_TOKEN"]
    i = 1
    while os.environ.get("VIDU_TOKEN_%d" % i):
        out[i] = os.environ["VIDU_TOKEN_%d" % i]
        i += 1
    return out


# ─────────────────────── 客户端 ───────────────────────
class Vidu:
    def __init__(self, token=None, account=None):
        t = tokens()
        if token is None:
            k = account if account is not None else int(os.environ.get("VIDU_DEFAULT_ACCOUNT", "1"))
            token = t.get(k) or t.get(0)
            if not token:
                raise SystemExit("未配置 VIDU_TOKEN：请在 .env 里写 VIDU_TOKEN=%s 或 VIDU_TOKEN_%d" % ("..." , k))
        self.token = token
        self.h = {"Authorization": "Token " + token,
                  "Content-Type": "application/json",
                  "User-Agent": "manju-vidu-cli/1.0"}

    def req(self, method, path, payload=None, timeout=180):
        data = json.dumps(payload).encode() if payload is not None else None
        url = path if path.startswith("http") else base_url() + path
        r = urllib.request.Request(url, data=data, method=method, headers=self.h)
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:
            return -1, repr(e)

    def jget(self, path):
        c, b = self.req("GET", path)
        try:
            return c, json.loads(b)
        except Exception:
            return c, {"_raw": b[:300]}

    def quota(self):
        _, q = self.jget("/vidu/v1/tasks/quota")
        nq = q.get("nopeak_quota") or {}
        total, used = nq.get("total") or 0, nq.get("used") or 0
        _, me = self.jget("/credit/v1/credits/me")
        return {"nopeak_total": total, "nopeak_used": used,
                "nopeak_left": max(0, total - used) if total else 0,
                "credits": me.get("credits")}

    def inflight(self):
        _, j = self.jget("/vidu/v1/tasks/history/me?pager.pagesz=100&pager.page=0")
        return [t for t in (j.get("tasks") or []) if t.get("state") in INFLIGHT]

    @staticmethod
    def _probe(path, entries):
        try:
            return subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", entries,
                                   "-of", "csv=p=0", path],
                                  capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception:
            return ""

    def upload(self, path, kind="image"):
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if kind == "audio":
            meta = {"duration": self._probe(path, "format=duration") or "0"}
        else:
            wh = self._probe(path, "stream=width,height").replace(",", "x").split("x")
            meta = {"image-width": wh[0] if wh[0].isdigit() else "1024",
                    "image-height": wh[1] if len(wh) > 1 and wh[1].isdigit() else "1024"}
        c, b = self.req("POST", "/tools/v1/files/uploads", {"metadata": meta, "scene": "vidu"})
        if c != 200:
            raise RuntimeError("create_upload %s: %s" % (c, b[:200]))
        j = json.loads(b)
        uid, put = str(j.get("id") or ""), j.get("put_url") or ""
        if not uid or not put:
            raise RuntimeError("create_upload 返回异常: %s" % b[:200])
        ph = dict(j.get("headers") or {})
        if not any(k.lower() == "content-type" for k in ph):
            ph["Content-Type"] = mime
        with open(path, "rb") as f:
            blob = f.read()
        r = urllib.request.Request(put, data=blob, method="PUT", headers=ph)
        try:
            with urllib.request.urlopen(r, timeout=300) as resp:
                etag = resp.headers.get("ETag") or resp.headers.get("Etag") or ""
        except urllib.error.HTTPError as e:
            raise RuntimeError("PUT 上传失败 %s: %s" % (e.code, e.read()[:200]))
        c, b = self.req("PUT", "/tools/v1/files/uploads/%s/finish" % uid,
                        {"etag": etag, "id": uid})
        if c != 200:
            raise RuntimeError("finish %s: %s" % (c, b[:200]))
        return "ssupload:?id=%s" % uid

    def submit(self, payload):
        c, b = self.req("POST", "/vidu/v1/tasks", payload)
        if c != 200:
            raise RuntimeError("提交失败 %s: %s" % (c, b[:300]))
        return json.loads(b)

    def task(self, tid):
        return self.jget("/vidu/v1/tasks/%s" % tid)[1]

    def creations(self, tid):
        j = self.task(tid)
        cs = j.get("creations") or []
        if isinstance(j.get("creation"), dict):
            cs = [j["creation"]] + list(cs)
        return cs

    def download(self, url, dest, tries=3):
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        part = dest + ".part"
        for _ in range(tries):
            try:
                r = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                n = 0
                with urllib.request.urlopen(r, timeout=300) as resp, open(part, "wb") as f:
                    while True:
                        b = resp.read(1 << 16)
                        if not b:
                            break
                        f.write(b)
                        n += len(b)
                if n < 1024:
                    raise RuntimeError("下载过小")
                os.replace(part, dest)
                return n
            except Exception:
                if os.path.exists(part):
                    try:
                        os.remove(part)
                    except Exception:
                        pass
        raise RuntimeError("下载失败: %s" % url[:80])


# ─────────────────────── jobs → payload ───────────────────────
def build_payload(job, cli, mode, upcache):
    settings = {
        "duration": int(job.get("duration") or 5),
        "model_version": job.get("model_version") or "3.4",
        "resolution": job.get("resolution") or "1080p",
        "aspect_ratio": job.get("ratio") or "9:16",
        "sample_count": 1,
        "schedule_mode": mode,
        "codec": "h265",
        "movement_amplitude": "auto",
        "use_trial": False,
    }
    prompts = [{"type": "text", "content": job["prompt"]}]
    for img in job.get("images") or []:
        p = img.get("path") if isinstance(img, dict) else img
        prompts.append({"type": "image", "content": _up(cli, p, "image", upcache)})
    for au in job.get("audios") or []:
        p = au.get("path") if isinstance(au, dict) else au
        prompts.append({"type": "audio", "content": _up(cli, p, "audio", upcache),
                        "name": os.path.basename(p)})
    return {"type": job.get("type") or "character2video",
            "input": {"prompts": prompts, "editor_mode": "normal", "enhance": True},
            "settings": settings}


def _up(cli, path, kind, cache):
    if not path or not os.path.isfile(path):
        raise RuntimeError("素材不存在: %s" % path)
    st = os.stat(path)
    k = "%s|%d|%d" % (os.path.abspath(path), st.st_size, int(st.st_mtime))
    if k not in cache:
        cache[k] = cli.upload(path, kind)
    return cache[k]


# ─────────────────────── 命令 ───────────────────────
def cmd_quota(a):
    n = a.account
    cli = Vidu(account=n)
    q = cli.quota()
    live = cli.inflight()
    print("闲时额度：%d/%d（余 %d）" % (q["nopeak_used"], q["nopeak_total"], q["nopeak_left"]))
    print("进行中：%d / 100" % len(live))
    print("积分：%s" % q["credits"])
    return 0


def cmd_accounts(_a):
    t = tokens()
    if not t:
        print("未配置任何账号（.env 里写 VIDU_TOKEN 或 VIDU_TOKEN_1..N）")
        return 1
    for n in sorted(t):
        try:
            q = Vidu(token=t[n]).quota()
            print("账号%-2s 闲时 %d/%d（余 %d）  积分 %s"
                  % (n or "-", q["nopeak_used"], q["nopeak_total"], q["nopeak_left"], q["credits"]))
        except Exception as e:
            print("账号%-2s ✗ %s" % (n or "-", e))
    return 0


def _load_jobs(path, only, limit):
    d = json.load(open(path, encoding="utf-8"))
    jobs = d.get("jobs") if isinstance(d, dict) else d
    if only:
        jobs = [j for j in jobs if j.get("id") == only or j.get("src_id") == only]
    if limit:
        jobs = jobs[:limit]
    return jobs


def cmd_run(a):
    cli = Vidu(account=a.account)
    q = cli.quota()
    print("账号闲时余量：%d（进行中 %d）" % (q["nopeak_left"], len(cli.inflight())))
    jobs = _load_jobs(a.jobs, a.job if a.job else None, a.limit)
    cache, ok, fail = {}, [], []
    for j in jobs:
        jid = j.get("id")
        try:
            payload = build_payload(j, cli, a.mode, cache)
            if a.dry_run:
                print("[dry] %-24s %s %ss %s 图%d 音%d"
                      % (jid, payload["type"], payload["settings"]["duration"],
                         payload["settings"]["resolution"],
                         len(j.get("images") or []), len(j.get("audios") or [])))
                continue
            r = cli.submit(payload)
            tid = str(r.get("id"))
            ok.append((jid, tid))
            print("%-24s → %s" % (jid, tid), flush=True)
        except Exception as e:
            fail.append((jid, str(e)))
            print("%-24s ✗ %s" % (jid, e), flush=True)
        time.sleep(a.pause)
    print("\n提交成功 %d｜失败 %d" % (len(ok), len(fail)))
    for jid, e in fail:
        print("  失败 %s：%s" % (jid, e))
    if ok and a.ledger:
        led = {}
        if os.path.isfile(a.ledger):
            try:
                led = json.load(open(a.ledger, encoding="utf-8"))
            except Exception:
                led = {}
        for jid, tid in ok:
            led[jid] = {"task_id": tid, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        json.dump(led, open(a.ledger, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("台账：%s" % a.ledger)
    return 0 if not fail else 1


def cmd_raw(a):
    txt = open(a.prompt_file, encoding="utf-8").read()
    job = {"id": "raw_" + time.strftime("%H%M%S"), "prompt": txt,
           "duration": a.duration, "ratio": a.ratio, "resolution": a.resolution,
           "images": [{"path": p} for p in a.image],
           "audios": [{"path": p} for p in a.audio],
           "type": "character2video"}
    cli = Vidu(account=a.account)
    payload = build_payload(job, cli, a.mode, {})
    if a.dry_run:
        print(json.dumps(payload["settings"], ensure_ascii=False))
        print("图 %d｜音 %d｜提示词 %d 字"
              % (len(a.image), len(a.audio), len(txt)))
        return 0
    r = cli.submit(payload)
    print("task_id=%s" % r.get("id"))
    return 0


# ─────────────────────── 生图（t2i / r2i） ───────────────────────
def _wh(path):
    o = Vidu._probe(path, "stream=width,height").replace(",", "x").split("x")
    try:
        return int(o[0]), int(o[1])
    except Exception:
        return 1024, 1024


def build_image_payload(job, cli, mode, upcache):
    """无 ref ⇒ text2image；有 ref ⇒ reference2image。

    ★ r2i 的每张参考图要**上传两次**：`content`（裁剪图）+ `src_imgs[0]`（原图），
      两个必须是**不同的 upload id**（实测：复用同一个 id 会丢参考）。
    """
    refs = job.get("refs") or []
    mv = IMG_MODELS.get(job.get("model") or "pro", job.get("model") or "3.2_image_2_5_pro")
    settings = {
        "duration": 0,
        "resolution": job.get("resolution") or "1080p",
        "aspect_ratio": job.get("ratio") or "16:9",
        "sample_count": int(job.get("count") or 1),
        "movement_amplitude": "auto",
        "schedule_mode": mode,
        "model_version": mv,
        "use_trial": False,
        "image_count": max(1, len(refs)),
        "codec": "h265",
    }
    prompts = [{"type": "text", "content": job["prompt"]}]
    for i, p in enumerate(refs, 1):
        a = _up(cli, p, "image", upcache)      # 裁剪图（走缓存）
        b = cli.upload(p, "image")             # 原图（★ 不复用缓存，必须另一个 id）
        w, h = _wh(p)
        prompts.append({"type": "image", "content": a, "src_imgs": [b],
                        "selected_region": {"top_left": {"x": 0, "y": 0},
                                            "bottom_right": {"x": w, "y": h}},
                        "name": "图%d" % i})
    return {"type": "reference2image" if refs else "text2image",
            "input": {"prompts": prompts, "editor_mode": "normal", "enhance": True},
            "settings": settings}


def quote_image(cli, payload):
    s = payload["settings"]
    q = ("/vidu/v1/tasks/credits?type=%s&settings.duration=0&settings.resolution=%s"
         "&settings.aspect_ratio=%s&settings.model_version=%s&settings.schedule_mode=%s"
         "&settings.sample_count=%d&settings.image_count=%d"
         % (payload["type"], s["resolution"], s["aspect_ratio"], s["model_version"],
            s["schedule_mode"], s["sample_count"], s["image_count"]))
    c, j = cli.jget(q)
    return j if isinstance(j, dict) else {}


def safe_name(s, n=40):
    s = "".join(ch for ch in (s or "image") if ch not in '\\/:*?"<>|\r\n\t').strip()
    return (s[:n] or "image")


def cmd_image(a):
    cli = Vidu(account=a.account)
    items = []
    if a.prompt:
        items.append((a.name or "", a.prompt))
    if a.prompt_file:
        with open(a.prompt_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "||" in line:
                    t, body = line.split("||", 1)
                    items.append((t.strip(), body.strip()))
                else:
                    items.append(("", line))
    if not items:
        print("没给提示词：用 --prompt 或 --prompt-file")
        return 1

    mode = a.mode
    if a.ref and mode == "nopeak":
        print("⚠ 参考图生图不支持闲时（平台报 reference2image nopeak mode is not allowed）"
              "⇒ 本条强制走 normal")
        mode = "normal"

    os.makedirs(a.out, exist_ok=True)
    cache, ok, fail = {}, 0, 0
    for idx, (nm, text) in enumerate(items, 1):
        job = {"prompt": text, "refs": a.ref, "model": a.model, "ratio": a.ratio,
               "resolution": a.resolution, "count": a.count}
        try:
            payload = build_image_payload(job, cli, mode, cache)
        except Exception as e:
            print("[%d/%d] ✗ 组装失败：%s" % (idx, len(items), e))
            fail += 1
            continue
        q = quote_image(cli, payload)
        cost, left = q.get("cost_credits"), q.get("current_credits")
        tag = "%s/%s" % (payload["type"], payload["settings"]["model_version"])
        if a.dry_run:
            print("[dry] %-16s %-24s %s 积分（余额 %s）｜%s"
                  % (nm or "image", tag, cost, left, text[:36]))
            continue
        print("[%d/%d] %-16s %-24s 预计 %s 积分（余额 %s）"
              % (idx, len(items), nm or "image", tag, cost, left), flush=True)
        try:
            r = cli.submit(payload)
        except Exception as e:
            print("      ✗ 提交失败：%s" % e)
            fail += 1
            continue
        tid = str(r.get("id"))
        print("      task_id = %s" % tid, flush=True)

        st, waited = "created", 0
        while waited < a.timeout:
            st = (cli.task(tid) or {}).get("state") or st
            if st in DONE:
                break
            time.sleep(a.interval)
            waited += a.interval
        if st != "success":
            print("      ✗ %s（%s）" % (st, (cli.task(tid) or {}).get("err_code") or ""))
            fail += 1
            continue

        cs = cli.creations(tid)
        got = 0
        for i, c in enumerate(cs):
            url = next((str(c[k]) for k in ("download_uri", "nomark_uri") if c.get(k)), None)
            if not url:      # ★ 只有 uri = 带水印中间态 ⇒ 不下
                continue
            base = safe_name(nm or text)
            name = base + (".png" if len(cs) == 1 else "_%d.png" % (i + 1))
            dest = os.path.join(a.out, name)
            cli.download(url, dest)
            got += 1
            print("      ✅ %s（%.1f MB）" % (dest, os.path.getsize(dest) / 1048576))
        if got:
            ok += 1
        else:
            print("      ⚠ success 但拿不到无水印地址（中间态）⇒ 稍后重跑同一命令即可")
            fail += 1

    if a.dry_run:
        print("\n[dry] 共 %d 条，未提交" % len(items))
        return 0
    print("\n出图成功 %d｜失败 %d" % (ok, fail))
    return 0 if not fail else 1


def cmd_pull(a):
    cli = Vidu(account=a.account)
    led = {}
    if a.jobs and os.path.isfile(a.jobs):
        d = json.load(open(a.jobs, encoding="utf-8"))
        jobs = d.get("jobs") if isinstance(d, dict) else d
        for j in jobs:
            if j.get("task_id"):
                led[j.get("id")] = {"task_id": j["task_id"]}
    if a.task:
        led[a.task] = {"task_id": a.task}
    if not led:
        print("没有可下载的 task_id（jobs 里没有 task_id 字段）")
        return 1
    out = a.out or "."
    n = 0
    for jid, rec in led.items():
        try:
            st = cli.task(rec["task_id"]).get("state")
            if st != "success":
                print("%-24s · %s（跳过）" % (jid, st))
                continue
            cs = cli.creations(rec["task_id"])
            total = len(cs)
            for i, c in enumerate(cs):
                url = next((str(c[k]) for k in ("download_uri", "nomark_uri", "uri")
                            if c.get(k)), None)
                if not url:
                    continue
                base = "%s_vidu" % jid if total == 1 else "%s_vidu_%d" % (jid, i + 1)
                dest = os.path.join(out, base + ".mp4")
                if not (os.path.exists(dest) and os.path.getsize(dest) > 1024):
                    cli.download(url, dest)
                n += 1
                print("%-24s → %s" % (jid, os.path.basename(dest)))
        except Exception as e:
            print("%-24s ✗ %s" % (jid, e))
    print("下载 %d 个文件" % n)
    return 0


def main():
    if not ensure_activated():
        return 3
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, help="用哪个账号（读 .env 的 VIDU_TOKEN_N）")
    ap.add_argument("--port", type=int, default=0, help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("quota").set_defaults(fn=cmd_quota)
    sub.add_parser("accounts").set_defaults(fn=cmd_accounts)

    p = sub.add_parser("run")
    p.add_argument("--jobs", required=True)
    p.add_argument("--job", default="")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--mode", choices=["nopeak", "normal"], default="nopeak")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--pause", type=float, default=1.0)
    p.add_argument("--ledger", default="")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("raw")
    p.add_argument("--prompt-file", required=True)
    p.add_argument("--image", action="append", default=[])
    p.add_argument("--audio", action="append", default=[])
    p.add_argument("--duration", type=int, default=5)
    p.add_argument("--ratio", default="9:16")
    p.add_argument("--resolution", default="1080p")
    p.add_argument("--mode", choices=["nopeak", "normal"], default="nopeak")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_raw)

    p = sub.add_parser("pull")
    p.add_argument("--jobs", default="")
    p.add_argument("--task", default="")
    p.add_argument("--out", default="")
    p.set_defaults(fn=cmd_pull)

    p = sub.add_parser("image", help="生图：文生图 t2i / 参考图生图 r2i（默认花积分）")
    p.add_argument("--prompt", default="", help="提示词（单条）")
    p.add_argument("--prompt-file", default="", help="批量：每行一条；『标题||正文』可指定文件名")
    p.add_argument("--ref", action="append", default=[], help="参考图（可多次；给了就是 r2i）")
    p.add_argument("--model", default="pro", help="fast / pro / base，或直接给 model_version")
    p.add_argument("--ratio", default="16:9")
    p.add_argument("--resolution", default="1080p")
    p.add_argument("--count", type=int, default=1, help="同一次出几张（sample_count）")
    p.add_argument("--out", default=".", help="输出目录")
    p.add_argument("--name", default="", help="单条时的文件名（不带扩展名）")
    p.add_argument("--mode", choices=["normal", "nopeak"], default="normal",
                   help="★ 默认 normal（花积分）；nopeak 仅 t2i 可用，r2i 会被平台 403")
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_image)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main() or 0)
