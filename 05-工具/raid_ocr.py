# -*- coding: utf-8 -*-
"""联盟突袭 · 截图 → 字段 的取字层

和 `raid_core.py` 的分工：那边纯逻辑（给 token 出记录），这边只管"怎么从像素里把字认出来"。
分开放是为了让内核能在没有模型的环境里回放测试。

三条实测结论决定了这里的写法（数据见 `raidtest.py`）：

1. **全屏只跑一次检测（det）**，用它找行、找成员名、找百分比。
   检测对"白字压深底"的 BOSS 名会整条漏掉（6 行漏 3 行），所以 BOSS 一律单独抠图。
2. **BOSS / 成员 / 伤害一律 `use_det=False` 单行识别**，放大 2 倍。
   单行 20ms，比带检测快一个数量级，也更准（README 里那条老结论在这块同样成立）。
3. **伤害数字不能只认一次**。同一块 ROI 实测 up=1 读对、up=2 读成 `2,158,17,750`
   （置信度 0.997，光看分数会被骗）。所以要多倍率读 + 千分位格式校验，
   再和 det 的结果投票；投不出就标存疑，不硬凑。
"""
from __future__ import annotations

import io
import re
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

import alliance_ocr as A
import raid_cli
import raid_core as rc


def tokens(img: Image.Image, eng) -> list:
    """全屏检测一次，转成 raid_core 要的 token 形态。

    **必须显式传 use_det/use_cls/use_rec 三个参数**：RapidOCR 实例会把上一次
    调用的 `use_det=False` 记住，之后不带参数再调就只跑识别、不返回 boxes
    （实测：rec 一次之后，`eng(img)` 返回 TextRecOutput，没有 .boxes，
    直接 AttributeError）。这是踩过的真坑，别图省事写成 `eng(img)`。
    """
    r = eng(img, use_det=True, use_cls=True, use_rec=True)
    out = []
    boxes = [] if r.boxes is None else list(r.boxes)
    txts = [] if r.txts is None else list(r.txts)
    scores = [] if r.scores is None else list(r.scores)
    for box, txt, sc in zip(boxes, txts, scores):
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        out.append(dict(x0=float(min(xs)), y0=float(min(ys)),
                        x1=float(max(xs)), y1=float(max(ys)),
                        text=str(txt), score=float(sc)))
    return out


def read_line(img: Image.Image, box, eng, up: int = 2):
    """单行识别（不跑检测）。box 是图片坐标。"""
    if not box:
        return "", 0.0
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(img.width, x1), min(img.height, y1)
    if x1 - x0 < 6 or y1 - y0 < 6:
        return "", 0.0
    c = img.crop((x0, y0, x1, y1))
    if up != 1:
        c = c.resize((c.width * up, c.height * up), Image.Resampling.LANCZOS)
    r = eng(c, use_det=False, use_cls=False, use_rec=True)
    txts = [] if r.txts is None else list(r.txts)
    scores = [] if r.scores is None else list(r.scores)
    if not txts:
        return "", 0.0
    return str(txts[0]).strip(), float(scores[0]) if scores else 0.0


def _boss_ok(t: str) -> bool:
    """这条读数像不像一个 BOSS 名：2~8 字、只含中日文/字母/数字/R 之类。"""
    t = rc.norm_text(t).replace(" ", "")
    if not (2 <= len(t) <= 8):
        return False
    return bool(re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9·]+", t))


def read_boss_det(img: Image.Image, box, eng, up: int = 2):
    """对 BOSS 那一小条跑一次「检测+识别」。

    为什么要多这一步：只跑识别（rec）时，框边只要切到图标残片，模型就会凭空多吐
    一个字（真机 `惠医师`），而且换裁剪边界结果会跳。带检测的流程会把"有没有字"
    先判一遍，残片不会被当字读出来——实测同一批 6 行 det 读出 5 行干净，
    剩下 1 行出 `¥早众` 这种垃圾串，用下面的可信度检查就能挡掉。
    """
    if not box:
        return ""
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(img.width, x1), min(img.height, y1)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return ""
    c = img.crop((x0, y0, x1, y1))
    if up != 1:
        c = c.resize((c.width * up, c.height * up), Image.Resampling.LANCZOS)
    r = eng(c, use_det=True, use_cls=True, use_rec=True)
    txts = [] if r.txts is None else list(r.txts)
    boxes = [] if r.boxes is None else list(r.boxes)
    if not txts:
        return ""
    best, best_w = "", -1.0
    for i, t in enumerate(txts):
        w = 0.0
        if i < len(boxes):
            w = max(p[0] for p in boxes[i]) - min(p[0] for p in boxes[i])
        if w > best_w:
            best, best_w = str(t), w
    return best


def read_boss(img: Image.Image, boxes, eng, tight: bool = False):
    """BOSS 名：**det 优先**，不可信时才退回「三个左边界各读一次、取多数」。

    为什么不让便宜的 rec 先走：`惠医师` 那次事故里，同一行三个裁剪边界的读数
    是 [医师, 惠医师, 惠医师] —— 多数票会选出带残字的结果。三个框都落在同一块
    区域上，只要残片位置固定，它们会一起错，投票没法自证。det 会先判"这段是不是
    字"，残片不会被当字读出来，所以主路径必须是 det。
    代价：每屏约 +1.5 秒（6 行、每行 300~400ms）。采集是离线活，准确率优先。
    """
    if not boxes:
        return "", 0.0
    if not isinstance(boxes, (list, tuple)) or (len(boxes) == 4 and isinstance(boxes[0], (int, float))):
        boxes = [boxes]

    # 已经按墨迹列收到"只管文字"的紧框时，单行识别就够准（实测 6/6），
    # 先走这条 20ms 的路；只有紧框不可用/读不出时才上 det。
    if tight and boxes:
        t_fast, s_fast = read_line(img, boxes[0], eng, up=2)
        if _boss_ok(t_fast):
            return rc.norm_text(t_fast), s_fast

    t_det = read_boss_det(img, boxes[0], eng, up=2)
    if _boss_ok(t_det):
        return rc.norm_text(t_det), 0.99

    vals = []
    for b in boxes:
        t, s = read_line(img, b, eng, up=2)
        if t:
            vals.append((t, s))
    if not vals:
        return rc.norm_text(t_det), 0.0
    voted, score = rc.vote_text(vals)
    if not _boss_ok(voted):
        t3, s3 = read_line(img, boxes[0], eng, up=3)
        if _boss_ok(t3):
            return rc.norm_text(t3), s3
    return voted, score


def read_damage(img: Image.Image, box, eng, det_value=None, det_raw=""):
    """伤害数字：多倍率读 + 千分位校验 + 与检测结果投票。

    返回 (数值|None, 原始串, 说明)。
    说明为空 = 干净；非空 = 要写进"存疑"计数让用户看一眼。
    """
    cands = []
    if det_value is not None:
        cands.append((int(det_value), det_raw, "det"))
    raws = []
    for up in (2, 1):
        t, s = read_line(img, box, eng, up=up)
        if not t:
            continue
        v, why = rc.normalize_damage(t)
        raws.append((t, s, v))
        if v is not None:
            cands.append((v, t, f"roi{up}"))

    if not cands:
        raw = raws[0][0] if raws else det_raw
        why = rc.normalize_damage(raw)[1] if raw else "读不出"
        return None, raw, why

    vals = {v for v, _, _ in cands}
    if len(vals) == 1:
        v = vals.pop()
        return v, cands[0][1], ""
    # 两档结果互相矛盾：检测值优先（实测它在大数字上最稳），但要留痕
    if det_value is not None:
        return int(det_value), det_raw, f"倍率读数不一致（{[c[1] for c in cands]}）"
    v = cands[0][0]
    return v, cands[0][1], "倍率读数不一致"



# 外部识别 CLI 需要知道「预设/」在哪；由窗口层注入（这里不反向 import alliance_ocr）
_APP_DIR = None


def set_app_dir(path):
    global _APP_DIR
    _APP_DIR = Path(path) if path else None


def _cli_text(img, box):
    """把一块裁图交给外部识别 CLI（未配置就返回空串）。

    只在本地识别**存疑**时调用：主链路不依赖它，配了就更准，没配行为完全不变。
    """
    if _APP_DIR is None or not box:
        return ""
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(img.width, x1), min(img.height, y1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return ""
    buf = io.BytesIO()
    img.crop((x0, y0, x1, y1)).save(buf, format="PNG")
    return raid_cli.read_text(_APP_DIR, buf.getvalue())


def red_mask(img: Image.Image):
    """等级徽标的颜色掩膜（红底白字，和深灰背景差别极大）。

    为什么用颜色而不是几何：真机 22:00 那一轮 det 把 4/6 行的 `LEVEL 10` 整条漏了，
    靠"成员名左上多少像素"去猜，换个分辨率就不准。红色徽标是画面里唯一的大块红，
    **定位它不需要任何比例假设**——这是把等级从"猜"变成"找"的关键。
    """
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return (r > 110) & (r - g > 55) & (r - b > 55)


def badge_box(mask, member_box):
    """在成员名左侧找红色徽标的外接框。"""
    if not member_box or mask is None:
        return None
    x0, y0, x1, y1 = member_box
    h = max(1.0, y1 - y0)
    cy = (y0 + y1) / 2.0
    sx0, sx1 = max(0, int(x0 - 4.0 * h)), max(0, int(x0) - 2)
    sy0, sy1 = max(0, int(cy - 1.3 * h)), int(cy + 0.9 * h)
    if sx1 - sx0 < 8 or sy1 - sy0 < 8:
        return None
    m = mask[sy0:sy1, sx0:sx1]
    if m.sum() < 30:
        return None
    ys, xs = np.where(m)
    return (sx0 + int(xs.min()), sy0 + int(ys.min()),
            sx0 + int(xs.max()) + 1, sy0 + int(ys.max()) + 1)


def _strip_border_blobs(w):
    """去掉与边界连通的白块。

    徽标框里除了白字，还夹着卡片的白边、页面底色 —— 它们和字一样是白的，
    靠颜色分不开，但它们都**连到框外**。文字是被红飘带包住的，所以从边界
    做一次洪泛，能到的一律不算字。就这么一句，`LEVEL 10` 的读数从
    `LEVELDO / LEVEL IOS` 变成干净的 `LEVEL 10`。
    """
    h, wd = w.shape
    seen = np.zeros((h, wd), dtype=bool)
    q = deque()
    for x in range(wd):
        for y in (0, h - 1):
            if w[y, x] and not seen[y, x]:
                seen[y, x] = True
                q.append((y, x))
    for y in range(h):
        for x in (0, wd - 1):
            if w[y, x] and not seen[y, x]:
                seen[y, x] = True
                q.append((y, x))
    while q:
        y, x = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < wd and w[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                q.append((ny, nx))
    return w & (~seen)


def badge_tile(img, box, ratio: float = 0.0):
    """把徽标裁成「白字 + 黑底」的纯文字小图。

    ratio=0 取全部墨迹行（默认）；ratio>0 按行/列墨迹量再收紧一档。
    """
    if not box:
        return None
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(img.width, x1), min(img.height, y1)
    if x1 - x0 < 20 or y1 - y0 < 12:
        return None
    sub = np.asarray(img.crop((x0, y0, x1, y1)).convert("RGB")).astype(np.int16)
    white = sub.min(axis=2) > 185
    ink = _strip_border_blobs(white)
    if ink.sum() < 30:
        return None
    rows = ink.sum(axis=1)
    cols = ink.sum(axis=0)
    rt = max(1.0, rows.max() * ratio)
    ct = max(1.0, cols.max() * ratio)
    rr = np.where(rows >= rt)[0]
    cc = np.where(cols >= ct)[0]
    if len(rr) < 6 or len(cc) < 10:
        return None
    tile = ink[rr[0]:rr[-1] + 1, cc[0]:cc[-1] + 1]
    return Image.fromarray((tile * 255).astype(np.uint8)).convert("RGB")


def ocr_tile(tile, eng, up: int = 6) -> str:
    """把小白图放大 + 加黑边后识别。放大是关键：原图字高只有 14px。"""
    if tile is None:
        return ""
    im = tile.resize((tile.width * up, tile.height * up), Image.Resampling.LANCZOS)
    pad = max(8, im.height // 3)
    cv = Image.new("RGB", (im.width + pad * 2, im.height + pad * 2), (0, 0, 0))
    cv.paste(im, (pad, pad))
    t, _s = read_line(cv, (0, 0, cv.width, cv.height), eng, up=1)
    return "" if t is None else str(t).strip()


# 读徽标的 8 个裁切组合，两族各 4 条：
#   f = 从徽标框左边按比例切（数字那一段），i = 去掉白边后的墨迹小图
# 为什么要两族：`f` 在小窗口（573 宽那种缩水截图）上很准，在 2320 上会把 `10`
# 读成 `1O0`；`i` 反过来，2320 上准、573 上因为字只有 5px 高而全糊。两族投票互补。
# f 的偏移是实测选的（2026-11-06 2320 分辨率，诊断包 r01~r06 逐张扫过）：
#   0.45 / 0.58 / 0.60 三路都稳定读出 `8`；
#   0.50 / 0.55 会把 `LEVEL 8` 尾巴的 `L` 粘成 `18`（危险）；
#   0.62 / 0.64 只剩 `B` / `A` 这类残骸，只贡献噪声——已删掉。
_LEVEL_CROPS = (("f", 0.45, 3), ("f", 0.58, 3), ("f", 0.60, 3),
                ("i", 0.00, 6), ("i", 0.00, 4), ("i", 0.20, 6),
                ("i", 0.20, 8), ("i", 0.30, 6))


def read_level_badge(img, mask, member_box, eng):
    """读红色 LEVEL 徽标上的数字。

    真机的徽标只有 85×45 px（字高 14px），单次识别靠不住，实测同一行换裁切
    就能读出 `LEVEL 10` / `LEVELI` / `LEVELY` / `LEVELS` 一大串。所以：

      1. **用颜色找徽标**——红底白字是全场唯一的大块红，不需要比例假设；
      2. **两族裁切各读一遍**：按比例切数字段（f）＋去白边后的墨迹小图（i）；
      3. **取重复出现 ≥2 次的那个值**；凑不齐两票就返回 None，交给上层记存疑。

    第 3 步是关键：`1` 和 `10` 猜错的代价太大，宁空缺也不硬猜。
    实测三个分辨率（573 / 2092 / 2320）都对：`10,10,10,10,10,9` 与 `9,9,9,9,9,8`。
    """
    box = badge_box(mask, member_box)
    if not box:
        return None, ""
    x0, y0, x1, y1 = box
    w = x1 - x0
    f_votes, i_votes, raws = {}, {}, []
    # eng=None 表示「本地不识别、只走外部」——测试用，也方便将来把本地 OCR 整个换掉
    for kind, arg, up in (_LEVEL_CROPS if eng is not None else ()):
        if kind == "f":
            t, _s = read_line(img, (x0 + w * arg, y0, x1, y1), eng, up)
        else:
            t = ocr_tile(badge_tile(img, box, arg), eng, up)
        if not t:
            continue
        raws.append(t)
        v = rc.parse_level(t)
        if v is not None:
            bucket = f_votes if kind == "f" else i_votes
            bucket[v] = bucket.get(v, 0) + 1

    # **按徽标宽度决定谁优先**（两族的可靠性随字号正好相反）：
    #   · 大字徽标（宽 ≥60，2320 分辨率那种 85×45）→ f 优先。实测 i 族 6 张里 4 张
    #     把 `LEVEL 8` 读成 `3`，且 4 路同票，两族混票时会盖过 f 族正确的 2 票 8。
    #   · 小徽标（0.7 缩放那种）→ i 优先。f 族会把 `LEVEL 9` 读成 `19`（L 粘成 1）。
    # 优先族凑不够 2 票就判存疑，不降级——同尺寸下另一族更不可信。
    order = (f_votes, i_votes) if w >= 60 else (i_votes, f_votes)
    for votes in order:
        if not votes:
            continue
        v, n = max(votes.items(), key=lambda kv: kv[1])
        if n >= 2:
            return v, (raws[-1] if raws else "")
        break
    if not (f_votes or i_votes):
        ext = _cli_text(img, box)                     # 本地一票都没拿到 → 问外部
        if ext:
            ev = rc.parse_level(ext)
            if ev is not None:
                return ev, ext
        return None, (raws[-1] if raws else "")
    return None, (raws[-1] if raws else "")


def light_band(arr, y0, y1, x0, x1, thr_ratio: float = 0.06):
    """在指定范围里找出"白字那一行"的上下边界（按行做亮像素投影，取最密的一段）。

    为什么不按"成员名底下多少像素"去切 BOSS 行：那是比例假设，换分辨率就可能切偏，
    切偏的后果是名字被裁掉一半（真机 `_`）。按亮度投影找"这一块里最像文字的一行"
    是自标定的——窗口多大都能找到，不需要知道字高。
    返回 (y0, y1) 或 None。
    """
    y0, y1, x0, x1 = int(y0), int(y1), int(x0), int(x1)
    if y1 - y0 < 6 or x1 - x0 < 10:
        return None
    crop = arr[max(0, y0):y1, max(0, x0):x1]
    if crop.size == 0:
        return None
    mn = crop.min(axis=2)
    ink = (mn > 150).sum(axis=1)
    if ink.max() <= 0:
        return None
    thr = max(1, int(ink.max() * thr_ratio))
    best, cur = None, None
    for i, v in enumerate(ink):
        if v >= thr:
            if cur is None:
                cur = [i, i]
            else:
                cur[1] = i
        else:
            if cur and (best is None or cur[1] - cur[0] > best[1] - best[0]):
                best = cur
            cur = None
    if cur and (best is None or cur[1] - cur[0] > best[1] - best[0]):
        best = cur
    if not best or best[1] - best[0] < 4:
        return None
    return y0 + best[0], y0 + best[1]


def light_span(arr, y0, y1, x0, x1, gap: int = 8, thr_ratio: float = 0.15):
    """在一条横带里找"白字"的左右边界（按墨迹列分组取最宽的一组）。

    为什么需要：BOSS 名左边贴着一个小图标，框只要切到它就多吐一个字
    （真机 `本铁匠` / `惠医师`）。按列分组再取最宽的一组，能把孤立的小图标
    和文字分开——这和名单场景定位昵称用的是同一个思路，只是判据换成"亮像素"。

    arr 是整屏的 numpy 数组（read_screen 里算一次复用）。
    返回 (x0, x1) 或 None。
    """
    y0, y1, x0, x1 = int(y0), int(y1), int(x0), int(x1)
    if y1 - y0 < 6 or x1 - x0 < 10:
        return None
    crop = arr[max(0, y0):y1, max(0, x0):x1]
    if crop.size == 0:
        return None
    mn = crop.min(axis=2)
    ink = mn > 150
    cnt = ink.sum(axis=0)
    thr = max(1, int((y1 - y0) * thr_ratio))
    idx = np.where(cnt >= thr)[0]
    if len(idx) < 3:
        return None
    groups, cur = [], [idx[0]]
    for a, b in zip(idx, idx[1:]):
        if b - a <= gap:
            cur.append(b)
        else:
            groups.append(cur)
            cur = [b]
    groups.append(cur)
    best = max(groups, key=lambda g: g[-1] - g[0])
    return x0 + int(best[0]), x0 + int(best[-1])


def read_screen(img: Image.Image, eng, toks=None, bosses=None, fields=None,
                roster=None):
    """一屏 → (Locate, 记录列表)。

    分工：det 负责「找到这一行」，等级/占比/BOSS 三个小字段一律按几何 ROI 重认。
    真机实测（2026-11-06 22:00）det 在 6 行里有 4 行完全没认出 `LEVEL 10`，
    导出后等级是空的——小字号红底白字对检测模型来说太细了。

    toks 只给测试用（可以塞一份"被删掉 LEVEL/百分比"的 token 列表，
    验证纯几何兜底是否成立）。

    fields 是字段标定表（见 raid_core.DEFAULT_FIELDS / 窗口的【标定字段】）：
    传了就按用户画的框切字段，没传就用真机实测的默认值。
    """
    loc = rc.locate(tokens(img, eng) if toks is None else toks, fields=fields)

    # 丢掉被列表边切掉的行：它们的 BOSS 行往往已经裁没，硬读会出 `_`/`本铁匠`
    cut = [c for c in loc.cards if c.get("partial")]
    if cut and len(cut) < len(loc.cards):
        loc.cards = [c for c in loc.cards if not c.get("partial")]
        loc.notes.append(f"跳过 {len(cut)} 条被列表边切到的行")

    mask = None
    arr = None
    for c in loc.cards:
        if c.get("boss_roi"):
            if arr is None:
                arr = np.asarray(img.convert("RGB")).astype(np.int16)
            rois = list(c.get("boss_rois") or [c["boss_roi"]])
            base = rois[0]
            band = light_band(arr, base[1], base[3], base[0], base[2])
            by0, by1 = (band if band else (base[1], base[3]))
            sp = light_span(arr, by0 - 1, by1 + 2, base[0], base[2])
            tight_box = None
            if sp:
                tight_box = (sp[0] - 3, by0 - 2, sp[1] + 4, by1 + 2)
                rois = [tight_box] + rois
            t, s = read_boss(img, rois, eng, tight=tight_box is not None)
            t = rc.norm_text(t)
            if bosses:
                canon, how = rc.match_boss(t, bosses)
                if canon:
                    c["boss"] = canon
                    if how != "exact":
                        c["boss_note"] = f"{t} → {canon}（{how}）"
                else:
                    c["boss"] = ""
                    c["boss_note"] = f"不在本期名单：{t[:8] or '空'}"
            elif _boss_ok(t):
                c["boss"] = t
            else:
                c["boss"] = ""
                c["boss_note"] = f"认不出：{t[:8] or '空'}"
            if not c["boss"]:
                ext = _cli_text(img, tight_box or rois[0])
                if ext:
                    if bosses:
                        canon, how = rc.match_boss(ext, bosses)
                        if canon:
                            c["boss"] = canon
                            ext = f"{ext} → {canon}（{how}·外部）"
                    elif _boss_ok(ext):
                        c["boss"] = rc.norm_text(ext)
                    if c["boss"]:
                        c["boss_note"] = f"外部识别：{ext}"
            c["boss_score"] = s

        # 等级：det 给了就不动；没给先用红色徽标定位（最可靠），最后才退回几何比例
        if c.get("level") is None and c.get("member_box"):
            if mask is None:
                mask = red_mask(img)
            lv, raw = read_level_badge(img, mask, c["member_box"], eng)
            if lv is not None:
                c["level"] = lv
                c["level_note"] = f"徽标:{raw}"

        if c.get("level") is None:
            seen = []
            for box, tag in ((c.get("level_roi"), "roi"), (c.get("level_roi_wide"), "roi宽")):
                if not box:
                    continue
                t, _s = read_line(img, box, eng, up=2)
                lv = rc.parse_level(t)
                if lv is not None:
                    c["level"] = lv
                    c["level_note"] = tag
                    break
                seen.append(t[:12] or "空")
            if c.get("level") is None:
                c["level_note"] = "未识别：" + " / ".join(seen)

        # 百分比：同理，det 漏了就抠伤害下方那一块
        if not c.get("pct") and c.get("pct_roi"):
            if arr is None:
                arr = np.asarray(img.convert("RGB")).astype(np.int16)
            pr = c["pct_roi"]
            band = light_band(arr, pr[1], pr[3], pr[0], pr[2])
            box = pr
            if band:
                box = (pr[0], band[0] - 2, pr[2], band[1] + 2)
            t, _s = read_line(img, box, eng, up=2)
            if rc.PCT_RE.match(rc.norm_text(t)):
                c["pct"] = rc.norm_text(t)

        dmg, raw, why = read_damage(img, c["damage_box"], eng,
                                    c.get("damage"), c.get("damage_raw", ""))
        c["damage"] = dmg
        c["damage_raw"] = raw
        c["damage_note"] = why

        # 成员名归位：伤害页上的人就是联盟成员，名单场景攒下的昵称库（真名 +
        # 别名表）直接可用。判据全在 roster_cand 里——等长、书写系统一致、
        # 相似度 ≥0.62、库里恰好一个候选，缺一不表态，宁可不改。
        # 实测价值：`丽月夜`→`朧月夜`（首卡被日期栏压住时读歪）这类直接归位。
        if roster and c.get("member"):
            entries = roster.get("names") if isinstance(roster, dict) else None
            names = set(entries) if entries else (roster if not isinstance(roster, dict) else set())
            alias = {}
            for _n, _e in (entries or {}).items():
                for _a in (_e or {}).get("alias") or ():
                    alias[_a] = _n
            fixed, _orig = A.roster_fix(c["member"], names, alias)
            if fixed != c["member"]:
                c["member_note"] = f"库归位 {c['member']}→{fixed}"
                c["member"] = fixed

    # 几何越界 + BOSS 又读不出来 = 这行确实被列表边切掉了，丢掉让相邻屏去补
    cut2 = [c for c in loc.cards if c.get("clipped") and not c.get("boss")]
    if cut2 and len(cut2) < len(loc.cards):
        loc.cards = [c for c in loc.cards if c not in cut2]
        loc.notes.append(f"跳过 {len(cut2)} 条 BOSS 行被列表边切掉的行")

    recs = rc.attach_days(loc.cards, loc.days)
    recs, miss_head = rc.fill_days([dict(r) for r in recs])
    if miss_head:
        loc.notes.append(f"开头 {miss_head} 条没有日期标记（从中间开始采？）")
    return loc, recs
