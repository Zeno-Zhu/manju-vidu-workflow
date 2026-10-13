# -*- coding: utf-8 -*-
# (c) manju-vidu-workflow · MJVID:1de313acfa · 溯源标记，请保留
"""vidu_log.py —— 运行留痕（红线：长任务必须留痕）。

★ 机器级状态**只写 `~/.manju-vidu/`**，绝不写进 skill 目录
  （skill 目录会被升级/`git pull` 覆盖）。
"""
from __future__ import annotations

import json
import os
from datetime import datetime

STATE_DIR = r"C:\Users\Administrator\.manju-vidu"
RUNLOG = os.path.join(STATE_DIR, "vidu_run.ndjson")

def log(obj):
    os.makedirs(STATE_DIR, exist_ok=True)
    obj = dict(obj)
    obj["ts"] = datetime.now().isoformat()
    with open(RUNLOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    print("   · " + json.dumps({k: v for k, v in obj.items() if k != "ts"}, ensure_ascii=False)[:200])
