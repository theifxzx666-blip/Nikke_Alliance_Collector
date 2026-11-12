"""外部识别脚本模板：把一张裁图发给「OpenAI 兼容」的视觉模型，打印识别结果。

用途
----
配到 `预设/识别后端.json` 的 `cmd` 里，作为本地 OCR 的兜底（只在存疑时调用）：

    {
      "enabled": true,
      "cmd": ["D:/Codex/Nikke/02_工位/10_数据管线/OCR采集/engine/.venv/Scripts/python.exe",
              "D:/Codex/Nikke/02_工位/10_数据管线/OCR采集/engine/外部识别_vlm.py", "{png}"],
      "timeout": 30
    }

需要环境变量（任选其一，用哪个由 BASE_URL/MODEL 决定）：

    SILICONFLOW_API_KEY   默认 https://api.siliconflow.cn/v1  模型 Qwen/Qwen2.5-VL-7B-Instruct
    OPENAI_API_KEY        默认 https://api.openai.com/v1      模型 gpt-4o-mini
    DASHSCOPE_API_KEY     默认 https://dashscope.aliyuncs.com/compatible-mode/v1

也可以全部手填：VLM_BASE / VLM_MODEL / VLM_KEY。

设计约束
--------
- **只输出一行纯文本**，不带解释、不带引号 —— 上层直接拿去做 `parse_level` / `match_boss`。
- 超时/网络错误/没 key，一律打印空行（退出码 0）；上层把空当「没认出来」，不会中断采集。
- 图片按原尺寸直传（这些裁图本来就只有几十像素高，不需要缩放）。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request

PROVIDERS = {
    "siliconflow": ("https://api.siliconflow.cn/v1", "Qwen/Qwen2.5-VL-7B-Instruct",
                    "SILICONFLOW_API_KEY"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini", "OPENAI_API_KEY"),
    "dashscope": ("https://dashscope.aliyuncs.com/compatible-mode/v1",
                  "qwen-vl-ocr", "DASHSCOPE_API_KEY"),
}

# 提示词按用途分：徽标只认 LEVEL 后面的数字，别的一律不输出
PROMPT_LEVEL = ("这是一张游戏界面里的小徽标裁图。只输出徽标上 LEVEL 后面的数字本身，"
                "不要输出 LEVEL、不要解释、不要加标点。看不清就输出空。")
PROMPT_BOSS = ("这是一张游戏界面里的 BOSS 名称裁图。只输出名称本身，"
               "不要解释、不要加标点。看不清就输出空。")


def pick_provider():
    for name, (base, model, env) in PROVIDERS.items():
        if os.environ.get(env):
            return base, model, os.environ[env]
    key = os.environ.get("VLM_KEY", "")
    return os.environ.get("VLM_BASE", ""), os.environ.get("VLM_MODEL", ""), key


def main() -> int:
    if len(sys.argv) < 2:
        print("")
        return 0
    path = sys.argv[1]
    base, model, key = pick_provider()
    if not (base and model and key):
        print("")                       # 没配置就当没认出来，安静退出
        return 0
    try:
        raw = open(path, "rb").read()
    except OSError:
        print("")
        return 0

    prompt = PROMPT_LEVEL if len(sys.argv) < 3 else (
        PROMPT_LEVEL if sys.argv[2] == "level" else PROMPT_BOSS)
    b64 = base64.b64encode(raw).decode("ascii")
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 16,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
    }
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode("utf-8"))
        txt = (data["choices"][0]["message"]["content"] or "").strip()
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
        txt = ""
    print(" ".join(txt.split()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
