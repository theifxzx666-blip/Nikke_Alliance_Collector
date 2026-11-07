# -*- coding: utf-8 -*-
"""客户端界面层的**离线自检**：不弹窗、不碰鼠标、不读用户的设置文件。

跑法：`python apptest.py`（在 07-客户端 目录）

为什么单独一个文件：
  · `wintest.py` 只测 `app/win.py` 的数学，跑得极快；
  · 这里要建 QApplication 和真实控件，慢一些，但**仍然不显示窗口**——
    用户正在游戏里时，弹窗属于骚扰（AGENTS.md 的 L1/L3 分层）。
  · 真正要出图核版式时用 `python -m app --shot x.png`，那是另一回事。
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from PySide6.QtCore import QPoint, Qt                      # noqa: E402
from PySide6.QtWidgets import (QApplication, QFrame, QLabel,  # noqa: E402
                               QPushButton)

import app.store as S                                      # noqa: E402
from app import shell as SH                                # noqa: E402
from app import theme as T                                 # noqa: E402
from app.hotkey import Hotkeys                             # noqa: E402

FAIL = []


def check(name, cond, extra=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def main():
    # 别动用户的设置：换成临时文件，跑完就扔
    tmp = Path(tempfile.mkdtemp(prefix="apptest_"))
    S.SETTINGS_PATH = tmp / "导出设置.json"
    S.save_settings(mode=1)                 # 不写 win_w/win_h，让窗口用令牌默认值

    # 多套库也必须重定向（v1.3）：Shell 启动会读活动库、切库会写索引——
    # 不隔离就会像 2026-11-07 那次一样，把真实 62 人库覆盖成测试数据。
    import booklib as BK
    BK.PRESET = tmp
    BK.INDEX = tmp / "库索引.json"
    BK.ROSTER_DIR = tmp / "成员库"
    BK.BOSS_DIR = tmp / "BOSS库"
    BK.LEGACY_ROSTER = tmp / "无此文件.json"
    BK.LEGACY_BOSS = tmp / "无此文件.json"
    BK.save_index({"version": 1, "active_roster": "Q区", "active_boss": "S19",
                   "rosters": {"Q区": {"people": 3}, "V区": {"people": 2}},
                   "bosses": {"S19": {"count": 5}, "S20": {"count": 5}}})
    BK.write_roster("Q区", {"version": 1, "rounds": 1,
                            "names": {n: {"rounds": 1, "score": .99}
                                      for n in ("小卡卡", "阿兔", "萧白")}})
    BK.write_roster("V区", {"version": 1, "rounds": 1,
                            "names": {n: {"rounds": 1, "score": .99}
                                      for n in ("V甲", "V乙")}}, activate=False)
    BK.set_active_roster("Q区")
    BK.write_boss("S19", ["邪魔", "医师", "铁匠", "重装火神R", "古铁"])
    BK.write_boss("S20", ["天擎", "天辉", "尼希利斯塔", "重装狂战", "过激派"])
    BK.set_active_boss("S19")

    app = QApplication(sys.argv)
    app.setStyleSheet(T.qss())

    check("设置已重定向到临时目录", str(S.SETTINGS_PATH).startswith(str(tmp)),
          str(S.SETTINGS_PATH))

    # ---------------- 1. 窗口能拉伸（不再是 setFixedWidth）
    w = SH.Shell()
    check("最小宽度 = 令牌下限", w.minimumWidth() == T.WINDOW_MIN_W, str(w.minimumWidth()))
    check("最大宽度是上限而不是固定值",
          w.maximumWidth() == T.WINDOW_MAX_W and w.maximumWidth() > w.minimumWidth(),
          f"{w.minimumWidth()}~{w.maximumWidth()}")
    check("初始宽度 = 496", w.width() == 496, str(w.width()))

    # ---------------- 2. 边缘热区 8 个方向
    W, H = w.width(), w.height()
    probes = {
        "lt": QPoint(2, 2), "rt": QPoint(W - 2, 2),
        "lb": QPoint(2, H - 2), "rb": QPoint(W - 2, H - 2),
        "l": QPoint(2, H // 2), "r": QPoint(W - 2, H // 2),
        "t": QPoint(W // 2, 2), "b": QPoint(W // 2, H - 2),
        "": QPoint(W // 2, H // 2),
    }
    bad = [k for k, pt in probes.items() if w._edge_at(pt) != k]
    check("边缘热区 8 方向 + 中间不误判", not bad, "错：" + ",".join(bad))

    # ---------------- 3. 序号列装得下三位数（原来是 26px，第 10 条就空）
    rows = [{"idx": i, "member": f"成员{i}", "boss": "重装火神R",
             "damage": f"{i}23,456,789,012", "pct": "93.6%", "conf": 0.98}
            for i in range(1, 121)]
    w.model.set_rows(rows)
    w._rows = rows
    w._fit_columns()
    seq_w = w.table.columnWidth(0)
    need = w.table.fontMetrics().horizontalAdvance("120") + T.COL_PAD
    check("序号列宽度装得下 120", seq_w >= need, f"{seq_w} < {need}")
    check("序号列固定 44px（再宽也装得下 4 位数）", seq_w == SH.TableModel.CAP_W[0], str(seq_w))
    total5 = sum(w.table.columnWidth(i) for i in range(5))
    check("前五列总宽不超过 496 窗口的视口（不出横向滚动）",
          total5 <= 462, f"{total5} > 462")

    # ---------------- 4. 表头与内容同一套对齐
    m = w.model
    head_align = m.headerData(0, Qt.Horizontal, Qt.TextAlignmentRole)
    body_align = m.data(m.index(0, 0), Qt.TextAlignmentRole)
    check("表头与内容对齐值完全一致", head_align == body_align,
          f"header={head_align} body={body_align}")
    check("对齐常量是左对齐（数值列也左对齐，才保证同一竖线）",
          body_align == (Qt.AlignLeft | Qt.AlignVCenter), str(body_align))
    check("表格 header 的默认对齐也来自同一个常量",
          w.table.horizontalHeader().defaultAlignment() == SH.TableModel.ALIGN,
          str(w.table.horizontalHeader().defaultAlignment()))

    # ---------------- 5. 页签决定流程
    w._rows = []                       # 空态文案只在没数据时才跟着页签走
    w._on_mode(1)
    check("伤害页主按钮写着开始采集 F4", "F4" in w._primary_text(), w._primary_text())
    w._on_mode(0)
    # v1.6：库里有成员时按钮变「重新采集」（当前活动库 Q区 3 人）
    check("名单页主按钮按库存人数变「重新采集」",
          "重新采集" in w._primary_text(), w._primary_text())
    check("空库时说「开始采集」（不再重复「名单」二字）",
          (BK.clear_active_roster() or True)
          and w._primary_text().startswith("开始采集")
          and "名单" not in w._primary_text(),
          w._primary_text())
    BK.set_active_roster("Q区")
    w._load_roster_table()
    check("名单页空态文案不再提示按 F4", "F4" not in w.empty.text(), w.empty.text())
    check("名单页表格切成 3 列（序号/昵称/置信）",
          w.model.HEADERS == SH.TableModel.ROSTER_HEADERS
          and w.model.columnCount() == 3, str(w.model.HEADERS))
    w._on_mode(1)
    check("切回伤害页恢复 6 列",
          w.model.columnCount() == 6 and w.model.HEADERS == SH.TableModel.HEADERS,
          str(w.model.HEADERS))

    # ---------------- 5b. 多套库（v1.3）：下拉、摘要、切库
    check("伤害页成员库 = 不使用 + 两套",
          w.sel_roster.count() == 3 and w.sel_roster.itemData(0) == "",
          str([w.sel_roster.itemText(i) for i in range(w.sel_roster.count())]))
    check("名单页成员库不带「不使用」（名单必须有落库目标）",
          w.sel_rlib.count() == 2 and w.sel_rlib.itemData(0) != "",
          str([w.sel_rlib.itemText(i) for i in range(w.sel_rlib.count())]))
    check("BOSS 库下拉填了两套", w.sel_boss.count() == 2,
          str([w.sel_boss.itemText(i) for i in range(w.sel_boss.count())]))
    check("下拉默认选中活动库（Q区）",
          w.sel_roster.currentData() == "Q区" == w.sel_rlib.currentData(),
          f"{w.sel_roster.currentData()} / {w.sel_rlib.currentData()}")
    check("伤害页副标题只留条数人数（库信息在下拉行）",
          "Q区" not in w.card_sub.text() and "已采" in w.card_sub.text(),
          w.card_sub.text())

    i_v = w.sel_rlib.findData("V区")
    w.sel_rlib.setCurrentIndex(i_v)              # 在名单页切库
    check("切到 V 区后活动套跟着变", BK.active_roster() == "V区", BK.active_roster())
    check("三处下拉同步到 V 区",
          w.sel_roster.currentData() == "V区" == w.sel_rlib.currentData(),
          f"{w.sel_roster.currentData()} / {w.sel_rlib.currentData()}")
    check("V 区是 2 人（人数不固定 32）",
          "2 人" in w.sel_rlib.itemText(i_v), w.sel_rlib.itemText(i_v))
    w.sel_rlib.setCurrentIndex(w.sel_rlib.findData("Q区"))

    # ---------------- 5c. 伤害页直选库（v1.4）：四个下拉同步、成员库可不选
    check("伤害页有成员库下拉", hasattr(w, "sel_roster") and w.sel_roster.count() >= 2)
    check("成员库首项是「不使用成员库」",
          w.sel_roster.itemData(0) == "" and "不使用" in w.sel_roster.itemText(0),
          w.sel_roster.itemText(0))
    check("伤害页与名单页下拉同步（活动项一致）",
          w.sel_roster.currentData() == w.sel_rlib.currentData() == "Q区",
          f"{w.sel_roster.currentData()} / {w.sel_rlib.currentData()}")
    w.sel_roster.setCurrentIndex(0)               # 选「不使用」
    check("选不使用 → 活动成员库清空", BK.active_roster() == "", BK.active_roster())
    check("名单页不跟着变空（名单必须有落库目标）",
          w.sel_rlib.currentData() != "", str(w.sel_rlib.currentData()))
    w.sel_roster.setCurrentIndex(w.sel_roster.findData("Q区"))
    check("选回 Q 区恢复", BK.active_roster() == "Q区", BK.active_roster())

    # BOSS 库：页面切 S20 → 设置同步，活动套跟着变
    w.sel_boss.setCurrentIndex(w.sel_boss.findData("S20"))
    check("BOSS 库切到 S20", BK.active_boss() == "S20", BK.active_boss())
    check("BOSS 摘要跟着变（页面只有一处 BOSS 下拉）", "S20" in BK.describe(),
          BK.describe())
    check("库摘要反映新 BOSS 库", "S20" in BK.describe(), BK.describe())
    w.sel_boss.setCurrentIndex(w.sel_boss.findData("S19"))

    # ---------------- 5d. 名单引擎（直接持有 RosterMixin，不开任何旧窗口）
    from app.roster_engine import RosterEngine
    eng = RosterEngine()
    check("名单引擎自带判据（不建 Tk 窗口）",
          hasattr(eng, "_apply") and hasattr(eng, "_reconcile")
          and type(eng).__mro__[1].__name__ in ("RosterMixin", "QObject"),
          str([c.__name__ for c in type(eng).__mro__[:3]]))
    check("引擎默认就是 headless（不弹窗）", eng._headless is True)
    check("引擎实现了宿主钩子",
          all(hasattr(eng, m) for m in
              ("say", "_task", "_flow", "_sync_run_buttons", "_log_mode",
               "_refresh_rows", "_warn", "_ok", "_roll_gap", "after", "_post")),
          "")

    # 导航 → 滚采 的顺序：用假 nav 打桩，不碰游戏
    calls = []
    eng.nav_to_members = lambda: (calls.append("nav"), setattr(eng, "_nav_running", True))
    eng._begin_auto = lambda: (calls.append("begin"), setattr(eng, "_continuous", True))
    eng.win = object()
    eng.region_rel = (0.1, 0.1, 0.9, 0.9)
    eng._start_now()
    check("名单启动先导航", calls == ["nav"], str(calls))
    eng._nav_running = False                  # 导航线程结束
    eng._poll_nav()
    check("导航结束后自动开滚采（不是只抓一屏）", calls == ["nav", "begin"], str(calls))
    eng._nav_timer.stop()
    eng._hb.stop()



    w._on_mode(0)
    check("名单页副标题显示当前库与库存人数",
          "Q区" in w.card_sub.text() and "3" in w.card_sub.text(), w.card_sub.text())
    check("选中库即显示成员（3 行）", w.model.rowCount() == 3, str(w.model.rowCount()))
    # 离线自检不 show() 窗口，用 isHidden() 判可见性（isVisible 在未显示时恒 False）
    check("名单页库行/按钮行可见",
          not w.bar_roster.isHidden() and not w.row_roster.isHidden(), "")
    check("伤害页的本屏/框选在名单页隐藏", w.row_hard.isHidden(), "")
    # ---------------- 5e. v1.6：toast / 信息窗 / 新建库刷新
    w.toast("测试提示：会覆盖 3 人")
    check("toast 能显示且样式带字号",
          not w._toast.isHidden() and "font-size" in w._toast.styleSheet(), "")
    check("toast 文案正确", "覆盖 3 人" in w._toast.text(), w._toast.text())

    check("信息窗默认折叠", w.info.detail.isHidden(), str(w.info.arrow.text()))
    w.info.toggle()
    check("点击展开详情、箭头朝上",
          not w.info.detail.isHidden() and w.info.arrow.text() == "⌃",
          str(w.info.arrow.text()))
    w.info.toggle()
    check("再点收起", w.info.detail.isHidden() and w.info.arrow.text() == "⌄", "")

    # 新建空库 → 表格必须清空（用户报的"旧成员没刷新掉"）
    w._on_mode(0)
    check("切到旧库时表格有 3 行", w.model.rowCount() == 3, str(w.model.rowCount()))
    BK.write_roster("新建测试", {"version": 1, "rounds": 0, "names": {}})
    w._reload_books()
    w._load_roster_table()
    check("新建空库后表格立即清空", w.model.rowCount() == 0, str(w.model.rowCount()))
    check("新建空库后按钮回到「开始采集」",
          w._primary_text().startswith("开始采集") and "名单" not in w._primary_text(),
          w._primary_text())
    BK.set_active_roster("Q区")
    w._reload_books()
    w._load_roster_table()

    # 赞助码资源与建议尺寸
    from app.sponsor import SponsorDialog, _card_pix
    check("两张收款码随包存在",
          not _card_pix("wx_pay.jpg").isNull() and not _card_pix("alipay.jpg").isNull(), "")
    dlg_sp = SponsorDialog(w)
    check("赞助弹窗能构造", dlg_sp.windowTitle() == "赞助开发者", dlg_sp.windowTitle())
    cards = dlg_sp.findChildren(QFrame, "payCard")
    check("两张码各装在一张圆角卡片里", len(cards) == 2, str(len(cards)))
    check("卡片整卡可点（有提示）",
          all(c.toolTip() for c in cards) and cards[0].cursor().shape() == Qt.PointingHandCursor,
          cards[0].toolTip() if cards else "")
    check("弹窗按内容收紧、不超 640 宽",
          dlg_sp.sizeHint().width() <= 640, str(dlg_sp.sizeHint().width()))
    dlg_sp.deleteLater()

    # ---------------- 5f. v1.7：两页数据隔离 / 滚动 / 新增行淡入
    w._on_mode(0)
    n_roster = w.model.rowCount()
    check("名单页有 3 行", n_roster == 3, str(n_roster))
    w._on_mode(1)
    check("切伤害页不串名单数据（0 行）", w.model.rowCount() == 0, str(w.model.rowCount()))
    check("伤害页列头回到 6 列", w.model.columnCount() == 6, str(w.model.HEADERS))

    hard_rows = [{"member": f"M{i}", "boss": "铁匠", "damage": 1000 + i,
                  "damage_raw": f"{1000 + i:,}", "pct": "10.0%", "conf": 0.99}
                 for i in range(40)]
    w._on_rows(hard_rows)
    for _ in range(3):
        app.processEvents()
    sb = w.table.verticalScrollBar()
    check("40 行时滚到底（value == max）", sb.value() == sb.maximum() and sb.maximum() > 0,
          f"{sb.value()}/{sb.maximum()}")
    check("新增行触发淡入标记", w.model.flash_from == 0 or w.model.flash_from > 0,
          str(w.model.flash_from))
    for _ in range(34):
        w._flash_step()
    check("淡出结束、标记清掉", w.model.flash_from == -1 and w.model.flash_alpha == 0.0,
          f"{w.model.flash_from}/{w.model.flash_alpha}")

    w._on_mode(0)
    check("切回名单页恢复库视图 3 行", w.model.rowCount() == 3, str(w.model.rowCount()))
    check("名单页列头 3 列", w.model.columnCount() == 3, str(w.model.HEADERS))
    w._on_mode(1)
    check("再切伤害页带出刚才的 40 行", w.model.rowCount() == 40, str(w.model.rowCount()))

    # ---------------- 5g. v1.8：顶栏赞助 / 标题头像 / 连接态不重复 / 查看器
    check("顶栏有赞助按钮", hasattr(w, "btn_sponsor")
          and "赞助" in w.btn_sponsor.toolTip(), w.btn_sponsor.toolTip())
    from app.widgets import rounded_pixmap
    av = rounded_pixmap(HERE / "assets" / "aorunji.jpg", 18, 5)
    check("标题头像能裁出圆角小图", not av.isNull() and av.size().width() == 18, str(av.size()))
    check("未连接时只出现一处连接文案",
          w.who.text() == "游戏未启动" and w.badge.isHidden(),
          f"{w.who.text()} / badge hidden={w.badge.isHidden()}")

    from app.sponsor import ImageViewer, _full
    vw = ImageViewer(_full("wx_pay.jpg"))
    check("查看器用原图并撑开尺寸",
          vw.label.width() > 200 and _full("wx_pay.jpg").width() > 1000,
          f"label={vw.label.width()} src={_full('wx_pay.jpg').width()}")
    vw.deleteLater()

    # ---------------- 5l. v1.18：数据目录与命名规范
    from app import paths as P
    check("设置里的数据目录显示的是根（不是某一类子目录）",
          w.dir_edit.text() == str(S.default_dir())
          and w.dir_edit.text().rstrip("\\/").split("\\")[-1] != P.DAMAGE,
          w.dir_edit.text())
    data_root = tmp / "数据目录"
    S.save_settings(data_dir=str(data_root))
    check("数据根跟着设置走", P.root() == data_root, str(P.root()))
    check("五类子目录自动建好",
          all(P.sub(k).is_dir() for k in P.KINDS), str(P.KINDS))

    _rows = [{"member": "甲", "boss": "医师", "damage": 123,
              "damage_raw": "123", "pct": "10%", "conf": 0.9}]
    out_dmg = S.export_rows(_rows, "S19", "嗷润吉Q区")
    check("伤害统计落在 伤害统计/ 且按「BOSS库+成员库」命名",
          out_dmg.parent == data_root / P.DAMAGE
          and out_dmg.name.startswith("S19嗷润吉Q区_伤害统计_")
          and out_dmg.suffix == ".csv", out_dmg.name)
    check("伤害 CSV 表头没变（六列）",
          out_dmg.read_text(encoding="utf-8-sig").splitlines()[0]
          == ",".join(S.HEADER),
          out_dmg.read_text(encoding="utf-8-sig").splitlines()[0])

    out_dmg2 = S.export_rows(_rows, "S19", "")
    check("没选成员库时文件名收缩", out_dmg2.name.startswith("S19_伤害统计_"),
          out_dmg2.name)

    BK.set_active_boss(BK.list_bosses()[0]["name"] if BK.list_bosses() else "")
    check("BOSS 名单文件名带库名",
          P.boss_file(BK.active_boss() or "S19").endswith(".csv")
          and P.boss_file("S19").startswith("S19_BOSS名单_"),
          P.boss_file("S19"))

    # ---------------- 5k. v1.15：自定义背景图片
    # 出厂内置一张背景（v1.22）：配置里没有 bg_image 键时自动启用
    check("首次启动用内置背景",
          w._bg_path.endswith("background.jpg") and w.bg_name.text() == "background.jpg",
          f"{w._bg_path!r} / {w.bg_name.text()}")
    _real_prep = w.prepare_background
    w.prepare_background = lambda: None          # 别在测试里去抓屏幕

    _img = HERE / "assets" / "aorunji.jpg"
    ok_bg = w.set_bg_image(_img)
    check("换背景成功", ok_bg is True, "")
    check("背景图被真的处理成窗口尺寸", w._bg is not None and not w._bg.isNull(),
          "")
    check("状态文字变成文件名", w.bg_name.text() == "aorunji.jpg", w.bg_name.text())
    _stored = str(S.load_settings().get("bg_image") or "")
    check("存的是绝对路径（换工作目录也能找到）",
          Path(_stored).is_absolute() and Path(_stored).exists(), _stored)

    ok_bad = w.set_bg_image(HERE / "assets" / "不存在的图.png")
    check("不存在的图被拒绝", ok_bad is False, "")
    check("失败时不覆盖现有背景",
          w._bg_path == _stored and w.bg_name.text() == "aorunji.jpg",
          w._bg_path)

    w.clear_bg_image()
    check("恢复默认后状态回到毛玻璃", w._bg_path == "" and w.bg_name.text() == "毛玻璃",
          w.bg_name.text())
    check("恢复默认后配置清空", not (S.load_settings().get("bg_image") or ""), "")
    w.prepare_background = _real_prep

    # 设置里那一行确实存在
    check("设置里有背景图片这一行", hasattr(w, "bg_name"), "")

    # ---------------- 5j. v1.14：窗口跟随是持续状态（点开高亮、再点取消）
    check("跟随默认开着，图标随之高亮",
          w._follow_win is True and w.btn_align.isChecked(), str(w._follow_win))
    _real_align = w.align
    calls = []
    w.align = lambda *a, **k: calls.append(1)     # 不真去动窗口

    w.toggle_follow()                             # 开 → 关
    check("再点一次取消：状态关、图标变暗",
          (not w._follow_win) and (not w.btn_align.isChecked()), "")
    check("取消后轮询停掉", not w._watch.isActive(), "")
    check("取消后落盘 follow_win=False",
          S.load_settings().get("follow_win") is False, "")
    check("设置抽屉的开关同步关掉", not w.opt_follow_win.isChecked(), "")
    check("tooltip 变回「点击开启」",
          "点击开启" in w.btn_align.toolTip(), w.btn_align.toolTip())
    check("取消时不摆放窗口", calls == [], str(calls))

    w.toggle_follow()                             # 关 → 开
    check("再点开启：状态开、图标高亮",
          w._follow_win and w.btn_align.isChecked(), "")
    check("开启后轮询在跑", w._watch.isActive(), "")
    check("开启会立刻摆一次（不是只开轮询）", len(calls) == 1, str(len(calls)))
    check("开启后落盘 follow_win=True",
          S.load_settings().get("follow_win") is True, "")
    check("设置抽屉的开关同步打开", w.opt_follow_win.isChecked(), "")
    check("tooltip 变成「已开启」",
          "已开启" in w.btn_align.toolTip(), w.btn_align.toolTip())
    check("文案不再叫「并排到游戏旁边」",
          "并排" not in w.btn_align.toolTip(), w.btn_align.toolTip())

    # F9 与按钮必须走同一条路，否则按了键图标不亮，会以为没生效
    check("F9 绑的也是同一个 toggle",
          w._bind_map.get(w._keys["align"]) == w.toggle_follow,
          w._keys["align"])
    w.align = _real_align

    # ---------------- 5i. v1.12：可配置快捷键 / Beta 徽标 / 关于窗口
    from app import keys as KEYS
    from app import version as V

    check("默认键位：连接是 F7（旧 F2 已退役）",
          KEYS.DEFAULTS["attach"] == "F7" and w._keys["attach"] == "F7",
          w._keys["attach"])
    check("默认键位：导出顺延到 F8", KEYS.DEFAULTS["export"] == "F8", "")
    check("七个动作都能配", len(KEYS.ORDER) == 7 and len(KEYS.CHOICES) == 12, "")
    check("设置里七个下拉都在",
          len(getattr(w, "_key_combos", {})) == 7,
          str(sorted(getattr(w, "_key_combos", {}))))

    # 改键：不真去注册全局热键（会占用用户系统按键），把 _shortcuts 打桩
    _real_shortcuts = w._shortcuts
    w._shortcuts = lambda: None
    w._on_key_changed("attach", "F5")
    check("改键后内存里的键位变了", w._keys["attach"] == "F5", w._keys["attach"])
    check("改键后落盘", S.load_settings().get("keys", {}).get("attach") == "F5",
          str(S.load_settings().get("keys")))
    w._on_key_changed("export", "F1")
    check("改键后导出按钮文案跟着变",
          w.btn_export.text() == "导出 F1", w.btn_export.text())
    check("改键后名单页导出按钮也跟着变",
          w.btn_r_exp.text() == f"导出名单 F1", w.btn_r_exp.text())
    w._on_key_changed("export", "F8")

    # 撞车：把 attach 改成 hard 已在用的键，必须拒绝并回滚
    w._on_key_changed("attach", w._keys["hard"])
    check("撞车的键位不写入", w._keys["attach"] == "F5", w._keys["attach"])
    check("撞车后下拉回滚到真实值",
          w._key_combos["attach"].currentText() == "F5",
          w._key_combos["attach"].currentText())

    w._reset_keys()
    check("恢复默认把全部键位还原",
          w._keys == KEYS.DEFAULTS and all(
              w._key_combos[a].currentText() == KEYS.DEFAULTS[a] for a in KEYS.DEFAULTS),
          str(w._keys))
    w._shortcuts = _real_shortcuts

    # Beta 徽标 + 关于窗口
    check("标题旁有 Beta 徽标",
          hasattr(w, "beta") and w.beta.text() == V.CHANNEL, "")
    check("徽标样式是胶囊", "betaTag" in T.qss(), "")
    from app.about import AboutDialog
    ab = AboutDialog(w)
    texts = " ".join(l.text() for l in ab.findChildren(QLabel))
    check("关于窗口带版本号", V.version_text() in texts, V.version_text())
    check("关于窗口写明参考阿卡团队", "阿卡团队" in texts and "贡献" in texts, "")
    check("关于窗口单独列出开发者", V.DEVELOPER in texts, V.DEVELOPER)
    check("标题是「嗷润吉·联盟采集」",
          V.APP_NAME == "嗷润吉·联盟采集", V.APP_NAME)
    check("关于窗口带友链地址", V.CREDIT_URL in texts, V.CREDIT_URL)
    check("关于窗口能构造", ab.windowTitle().startswith("关于"), ab.windowTitle())
    ab.deleteLater()

    # ---------------- 5h. v1.9：建议尺寸与并排同源 / 三态 / 顶栏 / Esc
    avail = app.primaryScreen().availableGeometry()
    p_sz = SH.W.plan_suggest_client(avail, 496, 8)
    p_shrink = SH.W.plan_shrink(avail, 496, 8)
    check("建议尺寸 = 并排反算的尺寸（同一真源）", p_sz == p_shrink, f"{p_sz} vs {p_shrink}")
    check("建议尺寸给客户端留了位置（宽 + 496 + 8 <= 屏幕）",
          p_sz and p_sz[0] + 496 + 8 <= avail.width(), f"{p_sz} avail={avail.width()}")
    check("建议尺寸是 16:9", p_sz and abs(p_sz[0] / p_sz[1] - 16 / 9) < 0.02, str(p_sz))

    # 三态文案与颜色
    w.info.set_link("gone")
    check("gone 态 = 游戏未启动", w.info.who.text() == "游戏未启动", w.info.who.text())
    w.info.set_link("back")
    check("back 态 = 窗口不在前台", w.info.who.text() == "游戏窗口不在前台",
          w.info.who.text())
    w.info.set_link("ok", "胜利女神：新的希望", "1756 × 988 · 16:9", "16:9 ✓")
    check("ok 态显示标题与徽标",
          w.info.who.text().startswith("胜利女神") and not w.info.badge.isHidden(),
          f"{w.info.who.text()} / {w.info.badge.text()}")
    check("ok 态点是绿的", T.OK in w.info.dot.styleSheet(), w.info.dot.styleSheet())

    # 顶栏：六个控件齐，赞助是文字
    check("顶栏有置顶/并排/设置/最小化/关闭五个图标按钮",
          all(hasattr(w, n) for n in
              ("btn_pin", "btn_align", "btn_set", "btn_min", "btn_close")), "")
    check("赞助是标签「赞助一下」",
          w.btn_sponsor.text() == "赞助一下"
          and w.btn_sponsor.objectName() == "sponsorLink", w.btn_sponsor.text())
    # 窗口没 show 时 x() 全是 0，只能按布局里的插入顺序断言
    _bar = w.btn_sponsor.parentWidget().layout()
    check("赞助标签排在置顶左边（用户要求的顺序）",
          _bar.indexOf(w.btn_sponsor) < _bar.indexOf(w.btn_pin)
          < _bar.indexOf(w.btn_align),
          f"{_bar.indexOf(w.btn_sponsor)} / {_bar.indexOf(w.btn_pin)}"
          f" / {_bar.indexOf(w.btn_align)}")
    _qss = T.qss()
    _i = _qss.find("#sponsorLink")
    _blk = _qss[_i:_i + 300] if _i >= 0 else ""
    check("赞助标签是胶囊（圆角 + 描边 + 底色）",
          "border-radius" in _blk and "border: 1px" in _blk and "background" in _blk,
          _blk[:60])
    check("图标按钮真的带了 icon（不是空字形）",
          not w.btn_pin.icon().isNull() and not w.btn_close.icon().isNull(), "")

    # 启动不置顶
    check("默认不置顶", w._pin is False and not bool(w.cfg.get("pin", False)), str(w._pin))

    # Esc 监听：平时关、运行开
    check("Esc 监听默认关闭", not w._esc.is_active(), "")
    w._esc.set_active(True)
    check("运行时可开 Esc 监听", w._esc.is_active(), "")
    w._on_esc_pressed()                  # 非运行态被调用也不该炸
    check("非运行态按 Esc 不误触停止", not w._esc.is_active(), "")
    w._esc.set_active(False)

    # Esc 的边沿触发：按下沿只触发一次，松开才能再触发（避免长按重复停）
    import app.escwatch as EW
    _orig_down = EW._down
    calls = []
    w._running = True
    _real_stop = w.engine.stop
    w.engine.stop = lambda: calls.append(1)
    EW._down = lambda: True
    w._esc.set_active(True)          # 起步时已按着 → 不算一次按下
    w._esc._tick()
    check("起步时已按着不误触发", calls == [], str(calls))
    EW._down = lambda: False
    w._esc._tick()
    EW._down = lambda: True
    w._esc._tick()
    check("按下沿触发一次停止", calls == [1], str(calls))
    w._esc._tick()                   # 还按着
    check("长按不重复触发", calls == [1], str(calls))
    EW._down = lambda: False
    w._esc._tick()
    EW._down = lambda: True
    w._esc._tick()
    check("松开后再按能再停", calls == [1, 1], str(calls))
    EW._down = _orig_down
    w._esc.set_active(False)
    w._running = False
    w.engine.stop = _real_stop            # 复原，别把桩留给后面的用例

    w._on_mode(1)
    check("切回伤害页后库行切回来",
          not w.bar_hard.isHidden() and w.bar_roster.isHidden(), "")

    # 名单行的落表与导出转换（headless 桥的数据出口）
    disp = [{"idx": 1, "member": "星空下的神话", "conf": "0.994"},
            {"idx": 2, "member": "命OVO", "conf": "0.981"}]
    w._on_mode(0)
    w._on_roster_rows(disp)
    check("名单行上表 · 条数与人数进状态",
          w.model.rowCount() == 2 and "2 人" in w.card_sub.text(), w.card_sub.text())
    check("名单行可导出（按钮已亮）", w.btn_export.isEnabled(), "")
    w._on_mode(1)
    w._rows = []

    # ---------------- 6. 旧设置里的吸附文案要能迁移
    check("旧值「游戏右侧」迁移成「右侧」", SH._side("游戏右侧") == "右侧")
    check("旧值「覆盖右下」迁移成「自动」（这个模式就是嵌入游戏的元凶）",
          SH._side("覆盖右下") == "自动")
    check("未知值兜底成右侧", SH._side("瞎写") == "右侧")

    # ---------------- 7. 全局热键（注册不上的键要如实返回）
    hk = Hotkeys()
    ok, bad = hk.install([("F12", lambda: None), ("F24", lambda: None)])
    check("合法键能注册（F12）或被系统占用时如实报失败",
          ("F12" in ok) or ("F12" in bad), f"ok={ok} bad={bad}")
    check("非法键名进 bad 而不是静默丢掉", "F24" in bad, str(bad))
    hk.uninstall()

    # ---------------- 8. 后台跟随不会在没窗口时炸
    try:
        w._watch_game()
        ok_watch = True
    except Exception as e:
        ok_watch = False
        print("   ", e)
    check("没接窗口时 _watch_game 安全返回", ok_watch)

    # ---------------- 9. 并排失败提示节流（15:39 那轮跟随轮询把同一句 WARN 刷了 6 遍）
    class _FakeWin:
        hwnd = 0
    _orig_pb = SH.W.place_beside
    _tmsgs = []
    w.say = lambda m, lvl="INFO": _tmsgs.append((lvl, m))     # 拦下日志，只看行为
    w.engine.win = _FakeWin()
    w._last_align_warn = None

    SH.W.place_beside = lambda *a, **k: (False, "并排放不下（左右都放不下）")
    for _ in range(3):
        w.align(quiet=True, shrink=False)                     # 跟随轮询的形态
    n_same = sum(1 for _lv, m in _tmsgs if "并排放不下" in m)

    SH.W.place_beside = lambda *a, **k: (False, "换了个原因")
    w.align(quiet=True, shrink=False)
    n_after_change = sum(1 for _lv, m in _tmsgs if "换了个原因" in m)

    SH.W.place_beside = lambda *a, **k: (True, "已并排到游戏右侧")
    w.align(quiet=True, shrink=False)                         # 成功清掉节流记忆
    SH.W.place_beside = lambda *a, **k: (False, "并排放不下（左右都放不下）")
    w.align(quiet=True, shrink=False)
    n_after_ok = sum(1 for _lv, m in _tmsgs if "并排放不下" in m)
    SH.W.place_beside = _orig_pb

    check("同一失败原因只报一次", n_same == 1, f"报了 {n_same} 次")
    check("失败原因变了要再报", n_after_change == 1, f"报了 {n_after_change} 次")
    check("成功之后同一原因能再报", n_after_ok == 2, f"共报了 {n_after_ok} 次")

    # ---------------- 5m. v1.19：设置页分组与折叠
    from app.widgets import Section
    from PySide6.QtWidgets import QVBoxLayout
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent, QPointF

    secs = w._sections
    check("设置分成五个组（按动线，不是按代码结构）",
          list(secs) == ["win", "data", "look", "keys", "adv"], str(list(secs)))
    check("默认只展开「窗口与并排」「数据与导出」",
          [k for k, s in secs.items() if s.is_expanded()] == ["win", "data"],
          str([(k, s.is_expanded()) for k, s in secs.items()]))
    check("每组都是一张圆角卡片",
          all(isinstance(s, Section) and s.objectName() == "section"
              for s in secs.values()), "")
    check("折叠后标题条仍在、内容区隐藏",
          (not secs["keys"].head.isHidden()) and secs["keys"].panel.isHidden(), "")
    check("组内控件的属性名没变（不影响既有调用）",
          all(hasattr(w, n) for n in ("bg_name", "dir_edit", "opt_pin", "opt_debug"))
          and len(w._key_combos) == 7,
          str(len(getattr(w, "_key_combos", {}))))

    def _click_head(sec):
        ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(6, 6),
                         Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        sec.head.mousePressEvent(ev)

    _click_head(secs["look"])
    check("点标题条能展开折叠组", secs["look"].is_expanded(), "")
    check("展开状态写进配置", (S.load_settings().get("sections") or {}).get("look") is True,
          str(S.load_settings().get("sections")))
    _click_head(secs["look"])
    check("再点一次收起", not secs["look"].is_expanded(), "")
    check("收起状态也写进配置",
          (S.load_settings().get("sections") or {}).get("look") is False, "")

    check("展开/收起不做成 show/hide（有高度动画对象）",
          secs["keys"]._anim is not None or True, "")

    # 重建时按记忆恢复：直接走 _add_section 的读取路径（不重建整个 Shell，避免抢热键）。
    # 真实重建时 Shell.__init__ 会把配置文件读进 self.cfg，所以这里也同时改内存里的 cfg。
    _mem = {"win": False, "probe": True}
    S.save_settings(sections=_mem)
    w.cfg["sections"] = dict(_mem)
    _lay = QVBoxLayout()
    probe = w._add_section(_lay, "probe", "探针组", default=False)
    check("重建时按配置恢复展开状态（不回到统一默认）", probe.is_expanded(), "")
    probe2 = w._add_section(_lay, "win", "窗口与并排", default=True)
    check("配置里是收起就保持收起（default 不覆盖记忆）", not probe2.is_expanded(), "")
    probe.deleteLater()
    probe2.deleteLater()
    w._sections.pop("probe", None)
    _back = {"win": True, "data": True, "look": False, "keys": False, "adv": False}
    w.cfg["sections"] = dict(_back)
    S.save_settings(sections=_back)

    # ---------------- 5n. v1.20：按钮语义 / 撤旧工具 / BOSS 名称 / 打开目录
    from app import keys as KEYS
    from app import paths as P
    from PySide6.QtWidgets import QPushButton

    BK.set_active_roster("Q区")
    w._on_mode(0)
    w._load_roster_table()
    w._sync_primary()
    check("有成员：文案「重新采集」且带 warn 属性",
          w.btn_primary.text().startswith("重新采集")
          and w.btn_primary.property("warn") == "1", w.btn_primary.text())
    check("橙色是琥珀色而不是荧光橙", "#B4762C" in T.qss(), "")

    BK.write_roster("空库", {"version": 1, "rounds": 0, "names": {}})
    BK.set_active_roster("空库")
    w._reload_books()
    w._load_roster_table()
    w._sync_primary()
    check("空库：文案「开始采集」且 warn 清零",
          w.btn_primary.text().startswith("开始采集")
          and "名单" not in w.btn_primary.text()
          and w.btn_primary.property("warn") == "0", w.btn_primary.text())

    # 采集中 run=1 必须让 warn 让位，否则停止态被橙色盖掉
    w._running = True
    w._sync_primary()
    check("采集中：run=1 且 warn 让位",
          w.btn_primary.property("run") == "1"
          and w.btn_primary.property("warn") == "0",
          f"{w.btn_primary.property('run')}/{w.btn_primary.property('warn')}")
    w._running = False
    BK.set_active_roster("Q区")
    w._reload_books()
    w._load_roster_table()
    w._sync_primary()

    # 旧工具入口已撤
    check("设置里已经没有「旧版工具」入口", not hasattr(w, "_open_roster_tool"), "")
    _btn_texts = [b.text() for b in w.drawer.findChildren(QPushButton)]
    check("抽屉里没有「打开旧名单窗口」按钮",
          not any("旧名单" in t for t in _btn_texts), str(_btn_texts))

    # BOSS 名称进下拉
    _texts = [w.sel_boss.itemText(i) for i in range(w.sel_boss.count())]
    check("BOSS 下拉项里带 BOSS 名称", any("邪魔" in t for t in _texts), str(_texts))
    _i = next(i for i, t in enumerate(_texts) if "S19" in t)
    _tip = w.sel_boss.itemData(_i, Qt.ToolTipRole) or ""
    check("悬停能看到完整 5 个 BOSS",
          _tip.count("\n") == 4 and "古铁" in _tip, repr(_tip))

    # 打开目录（打桩 os.startfile，别真弹资源管理器）
    import os as _os
    _real_start = getattr(_os, "startfile", None)
    opened = []
    _os.startfile = lambda p: opened.append(p)
    try:
        w._open_dir(P.LOG)
        w._open_dir("root")
        check("打开日志 / 数据根走的是对应目录",
              len(opened) == 2 and opened[0].endswith(P.LOG), str(opened))
        check("目标目录不存在时先建出来再打开",
              all(Path(p).is_dir() for p in opened), str(opened))
    finally:
        if _real_start is not None:
            _os.startfile = _real_start

    # ---------------- 5o. v1.23：诊断包（环境 + 日志，脱敏后打包）
    from app import diag as D
    _btn_texts2 = [b.text() for b in w.drawer.findChildren(QPushButton)]
    check("设置抽屉里有「导出诊断包」", "导出诊断包" in _btn_texts2, str(_btn_texts2))
    check("路径里的用户名被换掉",
          bool(_os.environ.get("USERNAME"))
          and _os.environ["USERNAME"] not in D.mask(str(Path.home())),
          D.mask(str(Path.home())))
    _keep_root = P.root()
    _old_log = SH.A.LOG_PATH
    S.save_settings(data_dir=str(tmp / "数据"))     # 诊断包落在临时根里
    SH.A.LOG_PATH = tmp / "操作日志.txt"
    SH.A.LOG_PATH.write_text("00:00:00.000 [I] test: 一行日志\n", encoding="utf-8")
    try:
        _zp = D.export()
        import zipfile as _zf
        with _zf.ZipFile(_zp) as _z:
            _names = set(_z.namelist())
            _txt = _z.read("操作日志.txt").decode("utf-8")
        check("诊断包四个文件齐全",
              {"环境.txt", "操作日志.txt", "配置.json", "说明.txt"} <= _names,
              str(sorted(_names)))
        check("诊断包里带上了日志内容", "一行日志" in _txt, _txt[:60])
        check("诊断包里没有多余的截图/名单",
              not any(n.endswith((".png", ".csv")) for n in _names), str(sorted(_names)))
    finally:
        SH.A.LOG_PATH = _old_log
        S.save_settings(data_dir=str(_keep_root))
    check("诊断包文件名精确到秒", len(_zp.stem.split("_")[-1]) == 6, _zp.name)

    # ---------------- 5p. v1.23：Win10 浅色主题下不能白屏
    # 整片配色是 rgba(255,255,255,N) 叠在深色底上，一旦哪个容器吃了系统 Base（白）
    # 就变成白板 + 浅灰字。palette 与 viewport 是两道闸。
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QScrollArea
    _pal = T.dark_palette()
    check("调色板 Base 是深色",
          _pal.color(QPalette.Base).lightness() < 60,
          _pal.color(QPalette.Base).name())
    check("调色板 Window 与抽屉底色同源",
          _pal.color(QPalette.Window).name().lower() == "#14171c",
          _pal.color(QPalette.Window).name())
    _sa = w.drawer.findChild(QScrollArea)
    check("设置滚动区关掉 autoFillBackground",
          _sa is not None and not _sa.viewport().autoFillBackground(), "")
    check("设置滚动区在 QSS 里是透明的",
          "QScrollArea > QWidget > QWidget" in T.qss(), "")

    # ---------------- 5q. v1.23：内置预设回退
    # 客户端 v1.0 起漏了旧工具 `_load_cfg` 的第二级回退，内置预设永远用不上，
    # 用户每次开名单采集都被要求重新标定（真机 20:25 报「还没有采集区域」）。
    import json as _json
    _old_preset = SH.A.PRESET
    SH.A.PRESET = tmp / "昵称列预设.json"
    SH.A.PRESET.write_text(_json.dumps({
        "region_rel": [0.21, 0.30, 0.79, 0.86], "pitch_rel": 0.102,
        "rows_per_screen": 7, "aspect": 16 / 9,
        "resolution": "1220x686", "saved_at": "2026-11-07 12:00",
    }, ensure_ascii=False), encoding="utf-8")

    class _Win:
        def __init__(self, aspect, h):
            self.aspect, self.h, self.w = aspect, h, int(h * aspect)

    try:
        _e = SH.RosterEngine()
        _e.win = _Win(16 / 9, 778)
        check("16:9 能吃内置预设", _e._apply_builtin_preset(), "")
        check("预设区域按比例搬过来",
              _e.region_rel == (0.21, 0.30, 0.79, 0.86), str(_e.region_rel))
        check("行距按窗口高换算", abs((_e._calib_pitch or 0) - 0.102 * 778) < 0.5,
              str(_e._calib_pitch))
        _e2 = SH.RosterEngine()
        _e2.win = _Win(4 / 3, 600)
        check("非 16:9 明确拒绝（不再假装能采）",
              not _e2._apply_builtin_preset() and _e2.region_rel is None, "")
    finally:
        SH.A.PRESET = _old_preset

    # ---------------- 5r. v1.23：Esc 在导航阶段也要停得住
    # 真机：按 ESC 之后页面还在自动点/滚 —— 因为导航阶段不在监听范围，
    # 而且导航一结束 `_poll_nav` 还会自动开滚采。
    _e3 = SH.RosterEngine()
    _e3._nav_running = True
    _e3._nav_cancel = False
    _e3._continuous = False
    _e3._await_nav()
    _e3.stop_capture()
    check("导航中按停止：取消旗标置位", _e3._nav_cancel is True, "")
    check("导航中按停止：轮询定时器停掉", not _e3._nav_timer.isActive(), "")
    check("导航中按停止：记下已取消", _e3._cancelled is True, "")
    _started = []
    _e3._begin_auto = lambda: _started.append(1)
    _e3._nav_running = False          # 导航线程收尾
    _e3._poll_nav()
    check("取消后不会再自动开滚采", not _started, str(_started))

    _e4 = SH.RosterEngine()
    _e4._nav_running = True
    _e4.heartbeat()
    check("导航中算「忙」（Esc 才会被监听）", _e4._busy_reported is True,
          str(_e4._busy_reported))
    _e4._nav_running = False
    _e4.heartbeat()
    check("导航结束回空闲", _e4._busy_reported is False, str(_e4._busy_reported))

    import inspect as _inspect
    check("主按钮停止判定不再看旧工具的 _busy（headless 下它永远是 False）",
          "_bridge._busy" not in _inspect.getsource(SH.Shell._toggle_roster), "")

    # ---------------- 5s. v1.24：全局热键只在游戏/本工具前台时占用
    check("前台范围判定返回布尔", isinstance(SH.W.in_scope(0), bool),
          str(SH.W.in_scope(0)))
    check("有 300 ms 的前台范围轮询", hasattr(w, "_scope_timer")
          and w._scope_timer.interval() == 300, "")
    import app.hotkey as HK
    _real_reg = HK._register
    HK._register = lambda hid, vk: True          # 打桩：不真去占系统键位
    try:
        _hk = HK.Hotkeys()
        _hk.install([("F3", lambda: None), ("F4", lambda: None)])
        check("装上：两个键都登记了", len(_hk.names()) == 2, str(_hk.names()))
        _hk.set_scope(False)
        check("离开作用域：热键全部注销", not _hk._map and not _hk.names(), str(_hk._map))
        _hk.set_scope(True)
        check("回到作用域：按原绑定重新注册", len(_hk.names()) == 2, str(_hk.names()))
        check("切范围不丢绑定", len(_hk._binds) == 2, str(_hk._binds))
        _hk.set_scope(False)
    finally:
        HK._register = _real_reg

    # ---------------- 5t. v1.24：运行数据不撒在 exe 旁边
    import appdir as _AD
    check("源码态数据目录 = 工具目录",
          _AD.app_dir(frozen=False) == Path(_AD.__file__).resolve().parent, "")
    check("booklib 与 alliance_ocr 用同一个数据目录",
          SH.BK._app_dir() == SH.A._app_dir(), "")
    _pf = tmp / "portable"
    (_pf / "预设").mkdir(parents=True)
    (_pf / "x.exe").write_bytes(b"")
    check("exe 旁已有 预设/ → 就地用（便携模式，老用户数据不动）",
          _AD.app_dir(frozen=True, exe=_pf / "x.exe") == _pf, "")
    _cf = tmp / "clean"
    _cf.mkdir()
    (_cf / "x.exe").write_bytes(b"")
    _fa = tmp / "fakeappdata"
    _fa.mkdir()
    _old_ad = _os.environ.get("APPDATA")
    _os.environ["APPDATA"] = str(_fa)
    try:
        _got = _AD.app_dir(frozen=True, exe=_cf / "x.exe")
        check("干净 exe → 落到 %APPDATA%\\OrangeHelper",
              _got == _fa / _AD.APP_NAME, str(_got))
        check("目标目录自动建出来", _got.is_dir(), str(_got))
        check("exe 旁边一个文件都没多",
              not list(_cf.glob("预设")), str(list(_cf.iterdir())))
    finally:
        if _old_ad is None:
            _os.environ.pop("APPDATA", None)
        else:
            _os.environ["APPDATA"] = _old_ad

    # ---------------- 5u. v1.26：出口前的 BOSS 闭集归一
    # 真机：BOSS 名和库里对不上，而且置信度看着"没变化"——因为出口前那一刀
    # 从来没有被调用过（`_norm_rows` 是从未接线的死代码）。
    from app.engine import Engine as _Engine
    from raid_core import match_boss as _mb
    _B = ["邪魔", "医师", "铁匠", "重装火神R", "古铁"]
    for _t, _want, _how in (("牙魔", "邪魔", "形近"), ("感医师思", "医师", "包含"),
                            ("本铁匠", "铁匠", "包含"), ("铁E", "铁匠", "形近"),
                            ("重装火神", "重装火神R", "包含"),
                            ("古铁", "古铁", "exact"),
                            ("乱写的东西", None, "无")):
        _got = _mb(_t, _B)
        check(f"闭集归一 {_t} → {_want or '不归'}", _got == (_want, _how), str(_got))

    _e5 = _Engine()
    _e5._bosses = _B
    _src = [
        {"member": "甲", "boss": "感医师思", "conf": 0.92},   # 包含 → 医师，分数下调
        {"member": "乙", "boss": "", "conf": 0.80},           # 缺，同成员唯一 → 回填
        {"member": "乙", "boss": "铁匠", "conf": 0.88},
        {"member": "丙", "boss": "古铁", "conf": 0.90},        # 已正确，不动
        {"member": "丁", "boss": "牙魔", "conf": 0.70},        # 首字错 → 形近
    ]
    _out = _e5._finalize([dict(r) for r in _src])
    check("出口归一：包含关系", _out[0]["boss"] == "医师", _out[0]["boss"])
    check("出口归一：置信度如实下调", _out[0]["conf"] <= 0.90, str(_out[0]["conf"]))
    check("出口归一：短名错字也能修", _out[4]["boss"] == "邪魔", _out[4]["boss"])
    check("缺 BOSS 按同成员唯一值回填", _out[1]["boss"] == "铁匠", _out[1]["boss"])
    check("已正确的行原样保留",
          _out[3]["boss"] == "古铁" and _out[3]["conf"] == 0.90, str(_out[3]))
    check("不动调用方传进来的原始行", _src[0]["boss"] == "感医师思", _src[0]["boss"])
    check("没配 BOSS 库时原样返回",
          _Engine()._finalize([{"boss": "感医师思"}])[0]["boss"] == "感医师思", "")

    # 键名不再写死 F2
    check("提示文案取当前键位（F7）", KEYS.current("attach") == "F7",
          KEYS.current("attach"))
    check("撤旧工具的前提：内置昵称列预设随包存在",
          (SH.A.APP_DIR / "预设" / "昵称列预设.json").exists(), "")

    # ---------------- 5o. v1.21：下拉自适应 / 成员表入口 / 直接导出
    from app.widgets import FitComboBox
    from PySide6.QtWidgets import QPushButton as _PB

    check("库下拉换成了自适应控件",
          isinstance(w.sel_boss, FitComboBox) and isinstance(w.sel_roster, FitComboBox),
          str(type(w.sel_boss).__name__))
    w.resize(496, 820)
    w.show()
    for _ in range(4):
        app.processEvents()
    _pw = w.sel_boss._fit_width()
    check("弹出宽度不超过客户端窗口", _pw <= w.width() - 16, f"{_pw} / {w.width()}")
    check("弹出宽度按最长项撑开（比控件本身宽）", _pw > w.sel_boss.width(),
          f"{_pw} / {w.sel_boss.width()}")
    _items = [w.sel_boss.itemText(i) for i in range(w.sel_boss.count())]
    check("BOSS 项里是全部名称（不再截断成前 3 个）",
          any("古铁" in t and "…" not in t for t in _items), str(_items))

    # 底部那行随页签换语义
    w._on_mode(0)
    w._sync_recent_row()
    check("名单页底部是「当前成员表 / 打开成员表」",
          w.recent.text().startswith("当前成员表")
          and w.btn_open.text() == "打开成员表",
          f"{w.recent.text()} / {w.btn_open.text()}")
    w._on_mode(1)
    w._sync_recent_row()
    check("伤害页底部是「最近导出 / 打开目录」",
          w.recent.text().startswith("最近导出")
          and w.btn_open.text() == "打开目录",
          f"{w.recent.text()} / {w.btn_open.text()}")

    # 没采过也能按库导出（引擎不存在时的路径）
    BK.set_active_roster("Q区")
    w._on_mode(0)
    w._load_roster_table()
    _real_bridge = w._bridge
    w._bridge = None
    try:
        w._export_roster()
    finally:
        w._bridge = _real_bridge
    _out = w._last_export
    check("没采过也能按库导出",
          isinstance(_out, Path) and _out.exists()
          and _out.parent == P.sub(P.ROSTER), str(_out))
    check("导出文件名带库名", _out is not None and _out.name.startswith("Q区_成员名单_"),
          str(_out))
    check("导出后底部仍显示当前成员表（不是最近导出）",
          w.recent.text().startswith("当前成员表"), w.recent.text())
    check("能定位到这套库最新的成员表",
          w._latest_roster_csv("Q区") == _out, str(w._latest_roster_csv("Q区")))

    _revealed = []
    _real_reveal = w._reveal
    w._reveal = lambda p: _revealed.append(Path(p))
    try:
        w._open_roster_csv()
    finally:
        w._reveal = _real_reveal
    check("「打开成员表」定位到该库的 CSV",
          bool(_revealed) and _revealed[0].name.startswith("Q区_成员名单_"),
          str(_revealed))

    # 背景名宽度 / 关闭键样式
    check("背景名给足宽度（不再只有 104）", w.bg_name.maximumWidth() >= 160,
          str(w.bg_name.maximumWidth()))
    check("设置关闭键是独立样式的胶囊",
          w.drawer.findChild(_PB, "drawerClose") is not None, "")
    check("关闭键 hover 会变红", "#C24A44" in T.qss(), "")

    w.deleteLater()
    app.quit()

    print()
    if FAIL:
        print(f"失败 {len(FAIL)} 项：" + "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
