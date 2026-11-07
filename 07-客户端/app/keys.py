# -*- coding: utf-8 -*-
"""快捷键配置（v1.12）。

为什么单独一个模块：默认值、合法键、动作中文名要和设置界面、注册逻辑共用一份。
散在 shell.py 里就会「改一个忘一个」——尤其是工具提示里那句「F4」，改键之后
还挂着旧值，比没有提示更误导。

全局热键只支持 F 区（`hotkey.VK_F` 里就是 F1~F12）：RegisterHotKey 挂字母/数字键
会和游戏输入打架，这个口子不开。
"""

# 动作 → 默认键
DEFAULTS = {
    "attach": "F7",       # 连接/重查游戏窗口（旧版是 F2，用户要求改 F7）
    "roster": "F3",       # 切到名单页并开始/停止
    "hard": "F4",         # 切到伤害页并开始/停止
    "export": "F8",       # 导出当前页数据（旧版 F7 让给连接）
    "align": "F9",        # 并排到游戏旁边
    "settings": "F10",    # 设置抽屉
    "debug": "F11",       # 调试面板
}

# 设置界面里的展示顺序：常用动作在前，调试垫底
ORDER = [
    ("attach", "连接窗口"),
    ("roster", "名单页"),
    ("hard", "伤害页"),
    ("export", "导出"),
    ("align", "并排"),
    ("settings", "设置"),
    ("debug", "调试"),
]

LABELS = dict(ORDER)

# 可选键（全是全局可注册的 F 区功能键）
CHOICES = [f"F{i}" for i in range(1, 13)]

# 窗口内快捷键：不参与全局注册，也不需要配置（焦点在本窗口时才有意义）
LOCAL = {
    "shot": "F5",
    "pick": "F6",
    "pin": "Ctrl+P",
}


def load(cfg) -> dict:
    """从设置里读键位；缺项、脏值一律回落到默认值。"""
    raw = (cfg or {}).get("keys") if isinstance(cfg, dict) else None
    out = dict(DEFAULTS)
    if isinstance(raw, dict):
        for k in DEFAULTS:
            v = str(raw.get(k) or "").strip().upper()
            if v in CHOICES:
                out[k] = v
    return out


def current(action: str = "attach") -> str:
    """当前生效的键名（给各处提示文案用）。

    为什么要这个函数：文案里写死 "按 F2" 的地方散在 engine / infobar / shell 好几处，
    改键之后全部变成错指引。要显示键名就调它，别写死。
    """
    try:
        from . import store as S
        return load(S.load_settings()).get(action) or DEFAULTS.get(action, "")
    except Exception:
        return DEFAULTS.get(action, "")


def conflicts(keys: dict) -> list:
    """返回被重复占用的键名（去重）。空列表 = 没冲突。"""
    seen, dup = set(), []
    for k in DEFAULTS:
        v = keys.get(k)
        if not v:
            continue
        if v in seen:
            if v not in dup:
                dup.append(v)
        seen.add(v)
    return dup
