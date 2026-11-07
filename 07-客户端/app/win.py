# -*- coding: utf-8 -*-
"""窗口控制：强制 16:9 / 并排摆放 / 跟随游戏窗口。

两套坐标系必须分清，混用会差出一个 DPI 倍数（本机 150%）：
  · Win32（GetClientRect / SetWindowPos）用**物理像素**
  · Qt（setGeometry）用**逻辑像素**
模块内部统一按物理像素算，只把最终坐标换算成逻辑像素交给 Qt。

v1.1 改了「对齐」的语义（用户实测反馈：**不是把客户端嵌进游戏，是摆在旁边**）：
    plan_aspect(hwnd)            算 16:9 方案，不改任何东西
    fix_aspect(hwnd)             真改：把客户区调成 16:9，保留左上角
    plan_beside(...)             给定游戏客户区，算客户端摆哪（纯数学）
    plan_shrink(...)             放不下时，游戏该缩到多大（纯数学）
    place_beside(shell, hwnd)    **并排**：必要时还原/缩放游戏，然后摆客户端
"""
import ctypes
import time
from ctypes import wintypes

TARGET_ASPECT = 16 / 9
ASPECT_TOL = 0.012        # 1.2% 以内算合格：游戏自带黑边会带来一点误差

SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SW_RESTORE = 9

GAME_MIN_W = 900          # 再窄游戏菜单就点不准了，宁可不对齐也不缩到看不清
EDGE = 16                 # 游戏离工作区边缘留的余量


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def dpi_scale(hwnd) -> float:
    """该窗口所在显示器的缩放（1.5 = 150%）。拿不到就按 1.0。"""
    try:
        dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
        return float(dpi) / 96.0 if dpi else 1.0
    except Exception:
        return 1.0


def client_box(hwnd):
    """客户区在屏幕上的物理矩形 (x, y, w, h)。"""
    u = ctypes.windll.user32
    r = _RECT()
    if not u.GetClientRect(hwnd, ctypes.byref(r)):
        return None
    pt = wintypes.POINT(0, 0)
    if not u.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    w, h = int(r.right - r.left), int(r.bottom - r.top)
    if w <= 0 or h <= 0:
        return None
    return (int(pt.x), int(pt.y), w, h)


def window_box(hwnd):
    """整窗（含标题栏边框）的物理矩形。"""
    r = _RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return (int(r.left), int(r.top), int(r.right - r.left), int(r.bottom - r.top))


def is_maximized(hwnd) -> bool:
    return bool(ctypes.windll.user32.IsZoomed(hwnd))


def game_link(hwnd) -> str:
    """游戏窗口当前的通联状态：`gone` / `back` / `ok`。

    gone : 没连过、窗口已关、或最小化 —— 用户看到的是"游戏没开"
    back : 窗口在，但焦点不在它上面 —— 采集时滚轮发不进去，必须先点回来
    ok   : 前台可用

    只看 Win32 的窗口状态，不去猜进程名：进程还活着但窗口没了（崩溃重开、
    加载中）时，对用户来说一样是"连不上"。
    """
    if not hwnd:
        return "gone"
    u = ctypes.windll.user32
    try:
        if not u.IsWindow(hwnd):
            return "gone"
        if u.IsIconic(hwnd):            # 最小化：截图会拿到黑屏，等同没开
            return "gone"
        if u.GetForegroundWindow() != hwnd:
            return "back"
    except Exception:
        return "gone"
    return "ok"


def aspect_ok(w, h) -> bool:
    if not w or not h:
        return False
    return abs(w / h - TARGET_ASPECT) / TARGET_ASPECT <= ASPECT_TOL


def plan_aspect(hwnd):
    """算「把客户区改成 16:9」的方案；已经合格返回 None。

    优先**保宽度改高度**（玩家一般按宽度摆窗口），目标比现在高才改成保高度改宽度，
    两种情况都只缩不放——放大别人的窗口比缩小更容易撞到屏幕边界。
    """
    c = client_box(hwnd)
    if c is None:
        return None
    gx, gy, w, h = c
    if aspect_ok(w, h):
        return None
    target_h = int(round(w / TARGET_ASPECT))
    if target_h <= h:
        tw, th = w, target_h
    else:
        tw, th = int(round(h * TARGET_ASPECT)), h
    if tw <= 0 or th <= 0:
        return None
    win = window_box(hwnd)
    if win is None:
        return None
    _, _, ww, wh = win
    dw, dh = ww - w, wh - h              # 边框 + 标题栏占的像素
    return {"client": (w, h), "target_client": (tw, th),
            "window_size": (tw + dw, th + dh), "pos": (win[0], win[1]),
            "ratio": round(w / h, 4)}


def fix_aspect(hwnd):
    """真改窗口尺寸。返回 (是否改过, 说明)。"""
    if is_maximized(hwnd):
        return False, "窗口是最大化状态 —— 先还原再改比例"
    plan = plan_aspect(hwnd)
    if plan is None:
        return False, "比例已经是 16:9"
    tw, th = plan["window_size"]
    x, y = plan["pos"]
    ok = ctypes.windll.user32.SetWindowPos(
        hwnd, 0, int(x), int(y), int(tw), int(th),
        SWP_NOZORDER | SWP_NOACTIVATE)
    if not ok:
        return False, "SetWindowPos 失败 —— 游戏以管理员运行时，本程序也必须以管理员重启"
    cw, ch = plan["target_client"]
    return True, f"已改成 {cw}×{ch}（原 {plan['client'][0]}×{plan['client'][1]}）"


def _overlaps(cx, cy, cw, ch, gx, gy, gw, gh):
    return not (cx + cw <= gx or gx + gw <= cx or cy + ch <= gy or gy + gh <= cy)


def _logical(box, dpr):
    x, y, w, h = box
    return (int(round(x / dpr)), int(round(y / dpr)),
            int(round(w / dpr)), int(round(h / dpr)))


def plan_beside(client, cw, min_h, avail, side="右侧", gap=8, ch=None):
    """客户端该摆哪。纯数学，不动窗口。

    client : 游戏客户区**逻辑**矩形
    cw     : 客户端宽度（逻辑，用户可能拖过）
    ch     : 客户端当前高度（逻辑）。**给了就用它，不再拿游戏高度顶替**——
             用户明确要求"只对齐顶部和左侧，高度自己拉"（v1.4）；
             None 时退回旧行为（= 游戏客户区高），保留给老调用点。
    side   : 右侧 / 左侧 / 自动 / 不动
    返回 dict(x, y, w, h, side, overlap, note)。
    `overlap=True` 表示怎么摆都会压住游戏 —— 调用方必须先去缩游戏，**不许直接照这个摆**。
    """
    gx, gy, gw, gh = client
    g = int(gap)
    w = int(cw)
    h = max(int(ch) if ch is not None else int(gh), int(min_h))
    h = min(h, max(200, int(avail.height())))
    # 顶部对齐游戏；放不下就往上挪（优先保顶部，别让底边出屏）
    y = int(gy)
    if y + h > int(avail.bottom()):
        y = max(int(avail.top()), int(avail.bottom()) - h)
    if side == "不动":
        return {"x": gx, "y": y, "w": w, "h": h, "side": "不动",
                "overlap": False, "note": "按设置不并排"}

    right = (gx + gw + g, "右侧")
    left = (gx - w - g, "左侧")
    if side == "右侧":
        cands = [right]
    elif side == "左侧":
        cands = [left]
    else:                        # 自动：右优先，右放不下换左
        cands = [right, left]
    for x, name in cands:
        if int(avail.left()) <= x and x + w <= int(avail.right()):
            return {"x": x, "y": y, "w": w, "h": h, "side": name,
                    "overlap": False, "note": name}
    # 两边都放不下：报 overlap，由调用方先去缩游戏，**绝不默认盖上去**
    return {"x": gx, "y": y, "w": w, "h": h, "side": "无",
            "overlap": _overlaps(gx, y, w, h, gx, gy, gw, gh),
            "note": "左右都放不下"}


def plan_shrink(avail, cw, gap=8, margin=None):
    """并排放不下时，游戏客户区该缩到多大。返回 (w, h) 或 None（全是逻辑像素）。

    只缩不放：以工作区宽减掉客户端与间距反算；高度超出工作区就改以高为准。
    """
    m = EDGE if margin is None else int(margin)
    max_w = int(avail.width()) - int(cw) - int(gap) - 2 * m
    max_h = int(avail.height()) - 2 * m
    if max_w < GAME_MIN_W or max_h < GAME_MIN_W * 9 // 16:
        return None
    w = max_w
    h = int(round(w / TARGET_ASPECT))
    if h > max_h:
        h = max_h
        w = int(round(h * TARGET_ASPECT))
    if w < GAME_MIN_W:
        return None
    return (int(w), int(h))


def restore_window(hwnd) -> bool:
    """取消最大化。不先还原就 SetWindowPos，只会改"还原尺寸"，屏幕上一点变化都没有。"""
    try:
        u = ctypes.windll.user32
        if not u.IsZoomed(hwnd):
            return True
        u.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.22)
        return not bool(u.IsZoomed(hwnd))
    except Exception:
        return False


def set_client_size(hwnd, cw, ch, x=None, y=None) -> bool:
    """把客户区改成指定尺寸（**物理**像素），改完复验。不复验的调整等于没做。"""
    c = client_box(hwnd)
    win = window_box(hwnd)
    if not c or not win:
        return False
    dw, dh = win[2] - c[2], win[3] - c[3]      # 边框 + 标题栏占的像素
    nx = win[0] if x is None else int(x)
    ny = win[1] if y is None else int(y)
    ok = ctypes.windll.user32.SetWindowPos(
        hwnd, 0, nx, ny, int(cw) + dw, int(ch) + dh, SWP_NOZORDER | SWP_NOACTIVATE)
    if not ok:
        return False
    time.sleep(0.16)
    after = client_box(hwnd)
    return bool(after and abs(after[2] - int(cw)) <= 6 and abs(after[3] - int(ch)) <= 6)


def place_beside(shell, game_hwnd, side="右侧", gap=8, shrink=True):
    """把客户端摆到游戏**旁边**，两者零重叠。返回 (ok, 说明)。

    顺序（每步失败都老实说，**不降级成"盖上去"**）：
      1. 游戏最大化 → 还原
      2. 算位置；放不下 → 按工作区反算游戏尺寸并缩放（改完复验）
      3. 仍放不下 → 失败，告诉用户该手动做什么
    """
    if side == "不动":
        return True, "按设置不并排（F9 不移动窗口）"
    dpr = float(shell.devicePixelRatioF() or 1.0)
    scr = shell.screen()
    avail = scr.availableGeometry() if scr is not None else shell.geometry()
    cw = int(shell.width())
    ch = int(shell.height())          # v1.4：高度归用户，并排只对齐顶/左
    min_h = int(shell.minimumHeight())

    if not restore_window(game_hwnd):
        return False, "游戏窗口是最大化且还原失败 —— 需要以管理员运行本程序"
    c = client_box(game_hwnd)
    if c is None:
        return False, "拿不到游戏客户区，先接上游戏窗口"

    notes = []
    plan = plan_beside(_logical(c, dpr), cw, min_h, avail, side, gap, ch=ch)
    if plan["overlap"] and shrink:
        tgt = plan_shrink(avail, cw, gap)
        if tgt is None:
            return False, (f"屏幕可用宽 {avail.width()}px，放不下「游戏 + {cw}px 客户端」并排；"
                           f"请手动把游戏窗口调窄，或把客户端拖窄")
        # 游戏贴工作区左上角，右边正好空出客户端的位置
        px = int(round((avail.left() + EDGE) * dpr))
        py = int(round((avail.top() + EDGE) * dpr))
        if not set_client_size(game_hwnd, int(tgt[0] * dpr), int(tgt[1] * dpr), px, py):
            return False, ("调整游戏窗口失败 —— 游戏以管理员运行时，本程序也必须以管理员重启。"
                           "（不会把客户端盖到游戏画面上）")
        notes.append(f"游戏缩到 {tgt[0]}×{tgt[1]}")
        c = client_box(game_hwnd) or c
        plan = plan_beside(_logical(c, dpr), cw, min_h, avail, side, gap, ch=ch)

    if plan["overlap"]:
        return False, (f"并排放不下（{plan['note']}）：请手动把游戏窗口调窄，"
                       f"或把客户端拖窄后按 F9")

    x = max(int(avail.left()), min(int(plan["x"]), int(avail.right()) - plan["w"]))
    y = max(int(avail.top()), min(int(plan["y"]), int(avail.bottom()) - plan["h"]))
    shell.setGeometry(x, y, plan["w"], plan["h"])
    msg = f"已并排到游戏{plan['side']} · 客户端 {plan['w']}×{plan['h']} · 间距 {int(gap)}px"
    if notes:
        msg += "（" + "；".join(notes) + "）"
    return True, msg


def same_rect(a, b, tol=2) -> bool:
    """两个物理矩形是否等价 —— 跟随定时器用它判断"游戏动了吗"。"""
    if not a or not b:
        return False
    return all(abs(int(a[i]) - int(b[i])) <= tol for i in range(4))

def plan_suggest_client(avail, cw=496, gap=8, margin=None):
    """建议的目标客户区尺寸（逻辑像素）= **并排反算出来的尺寸**。

    v1.9 修正：早先这里只看屏幕、给出 1920×1080，完全没考虑客户端要占多宽，
    结果缩完游戏还是并排放不下（用户原话"建议尺寸明显不对，功能跟并排差不多"）。
    现在尺寸只有 `plan_shrink` 一个真源：可用宽 − 客户端宽 − 间距，再按 16:9 收敛。
    返回 (w, h) 或 None（屏幕根本放不下）。
    """
    return plan_shrink(avail, cw, gap, margin)
