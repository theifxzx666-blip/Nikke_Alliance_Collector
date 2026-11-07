# -*- coding: utf-8 -*-
"""设计令牌与全局样式表 —— 唯一真源。

规矩：页面里**不许**出现字面量颜色/字号/圆角，一律从这里取。
这条是"UI 不统一"的根治办法：改了令牌，全客户端一起变。
"""

# ---------------- 色板（沿用 GM 工具的毛玻璃画刷） ----------------
BG_SCRIM = (16, 18, 22, 209)          # 82% 压暗层：只上模糊不压暗，白字会糊
# 自定义背景用更松的压暗：82% 套在照片上等于把图压成一片黑（v1.15）
BG_SCRIM_CUSTOM = (16, 18, 22, 168)
SURFACE = "rgba(255,255,255,18)"      # 卡片底  ≈ #1CFFFFFF
SURFACE_STRONG = "rgba(255,255,255,28)"   # 主卡/选中行 ≈ #2BFFFFFF
BORDER = "rgba(255,255,255,31)"       # 1px 描边 ≈ #1FFFFFFF
HOVER = "rgba(255,255,255,38)"

TEXT = "#D6D9DE"
TEXT_DIM = "#8B9199"
ACCENT = "#2F6FD0"
ACCENT_HOVER = "#3D80E0"
OK = "#45B076"
OK_TEXT = "#7ED3A3"
WARN = "#E0A030"
WARN_TEXT = "#F0C070"
ERR = "#D9534F"

# ---------------- 尺寸 ----------------
RADIUS_WIN = 12
RADIUS = 8
PAD_CARD = 12
PAD_CARD_STRONG = 14
GAP = 12
H_BTN = 32
H_BTN_PRIMARY = 40
H_ROW = 30
H_TITLE = 40

FONT_UI = "Microsoft YaHei UI"
FONT_MONO = "Consolas"
SZ_TITLE = 13
SZ_BODY = 13
SZ_SUB = 11

WINDOW_W = 496        # v1.1 从 468 加宽：六列按字体实测后 468 装不下「置信」，
                      # 表现为最后一位被省略（0.98 → 0.9）。496 时视口 462，够。
WINDOW_H = 820        # v1.4 从 620 调高：用户要求"拉高一点，能看的信息多一点"。
                      # 已存的 win_h 优先（不覆盖用户自己拖过的尺寸）。
# 窗口自己拉伸（v1.1）：下限保证表格最窄时还能看清成员列，上限防拖出屏幕
WINDOW_MIN_W = 460
WINDOW_MIN_H = 560
WINDOW_MAX_W = 1600
WINDOW_MAX_H = 2000
HOT = 6               # 无边框窗口的边缘热区（逻辑像素）
COL_PAD = 8           # QSS 里 item/section 的左右 padding 合计（4×2）。
                      # 列宽 = max(表头 sectionSizeHint, 单元格 sizeHint + 这个值)


def qss() -> str:
    """生成全局样式表。所有组件只继承这里，不在页面里单独 setStyleSheet。"""
    return f"""
    QWidget {{ color: {TEXT}; font-family: "{FONT_UI}"; font-size: {SZ_BODY}px; }}
    QLabel[role="sub"] {{ color: {TEXT_DIM}; font-size: {SZ_SUB}px; }}
    QLabel[role="title"] {{ font-size: {SZ_TITLE}px; font-weight: 600; }}

    /* 卡片 */
    QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS}px; }}
    QFrame#cardStrong {{ background: {SURFACE_STRONG}; border: 1px solid {BORDER}; border-radius: {RADIUS}px; }}

    /* 普通按钮 */
    QPushButton {{
        background: rgba(255,255,255,16); border: 1px solid {BORDER};
        border-radius: {RADIUS}px; padding: 0 12px; min-height: {H_BTN}px; max-height: {H_BTN}px;
    }}
    QPushButton:hover {{ background: {HOVER}; }}
    QPushButton:pressed {{ background: rgba(255,255,255,10); }}
    QPushButton:disabled {{ color: #666B73; border-color: rgba(255,255,255,18); }}

    /* 主按钮 */
    QPushButton#primary {{
        background: {ACCENT}; border: 1px solid {ACCENT_HOVER}; color: #FFFFFF;
        font-weight: 600; min-height: {H_BTN_PRIMARY}px; max-height: {H_BTN_PRIMARY}px;
    }}
    QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
    QPushButton#primary[run="1"] {{ background: #A63A34; border-color: #C24A44; }}
    /* 名单页的重采态（v1.20）：点了会覆盖原名单，用琥珀橙提醒。
       不用 #FF8C00 那种荧光橙 —— 深色底上刺眼，也会和停止态的红抢注意力。 */
    QPushButton#primary[warn="1"] {{ background: #B4762C; border-color: #D79A4E; }}
    QPushButton#primary[warn="1"]:hover {{ background: #C4832F; border-color: #E3A85C; }}

    /* 图标按钮（标题栏） */
    QPushButton#icon {{
        min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px;
        padding: 0; border: none; border-radius: 6px; color: {TEXT_DIM}; background: transparent;
    }}
    QPushButton#icon:hover {{ background: {SURFACE}; color: {TEXT}; }}
    QPushButton#icon:checked {{ background: rgba(127,176,255,46); }}
    QPushButton#icon[on="1"] {{ background: rgba(47,111,208,46); color: #8AB4F8; }}
    /* 顶栏的「赞助一下」：胶囊标签，比图标轻、比纯文字显眼 */
    QPushButton#sponsorLink {{
        background: rgba(127,176,255,26);
        border: 1px solid rgba(127,176,255,72);
        border-radius: 10px;
        padding: 0 9px;
        min-height: 20px; max-height: 20px;
        color: #8FBDFF; font-size: {SZ_SUB}px;
    }}
    QPushButton#sponsorLink:hover {{
        background: rgba(127,176,255,52);
        border-color: rgba(127,176,255,140);
        color: #C0D8FF;
    }}
    /* 标题旁的 Beta 徽标：点开关于窗口 */
    QPushButton#betaTag {{
        background: rgba(224,160,48,28);
        border: 1px solid rgba(224,160,48,88);
        border-radius: 8px;
        padding: 0 6px;
        min-height: 18px; max-height: 18px;
        color: {WARN_TEXT}; font-size: {SZ_SUB}px;
    }}
    QPushButton#betaTag:hover {{
        background: rgba(224,160,48,55);
        border-color: rgba(224,160,48,150);
        color: #FFD79A;
    }}
    /* 赞助弹窗的收款码卡片：整卡可点，hover 亮边 */
    QFrame#payCard {{
        background: rgba(255,255,255,9);
        border: 1px solid rgba(255,255,255,26);
        border-radius: 12px;
    }}
    QFrame#payCard:hover {{
        background: rgba(255,255,255,16);
        border: 1px solid rgba(127,176,255,150);
    }}

    /* 分段控件 */
    QFrame#seg {{ background: rgba(255,255,255,13); border: 1px solid {BORDER}; border-radius: {RADIUS}px; }}
    QPushButton#segItem {{
        border: none; background: transparent; color: {TEXT_DIM};
        min-height: 28px; max-height: 28px; border-radius: 6px; padding: 0 17px;
    }}
    QPushButton#segItem[on="1"] {{ background: {SURFACE_STRONG}; color: {TEXT}; }}

    /* 表格 */
    QTableView {{
        background: transparent; border: none; gridline-color: transparent;
        selection-background-color: rgba(47,111,208,41); selection-color: {TEXT};
    }}
    QTableView::item {{ border-bottom: 1px solid rgba(255,255,255,13); padding: 0 4px; }}
    /* 表头底色必须自己给：默认吃系统 palette 的浅色 Button 色，
       半透明白压上去就是一条白板（离屏渲染实测过）。用不透明深色收口。 */
    QHeaderView {{ background: transparent; border: none; }}
    QHeaderView::section {{
        background: #23272F; color: {TEXT_DIM}; border: none;
        border-bottom: 1px solid {BORDER}; font-size: {SZ_SUB}px; padding: 0 4px; height: 28px;
    }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: rgba(255,255,255,36); border-radius: 4px; min-height: 24px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    /* 窗口能被拖到 420 宽，表格放不下时要能横向看（不加这条会直接裁掉右边的列） */
    QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: rgba(255,255,255,36); border-radius: 4px; min-width: 24px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    /* 日志 */
    QPlainTextEdit#log {{
        background: rgba(0,0,0,56); border: 1px solid {BORDER}; border-radius: {RADIUS}px;
        font-family: "{FONT_MONO}"; font-size: 11px; color: {TEXT_DIM}; padding: 4px 6px;
    }}

    /* 进度条 */
    QProgressBar {{
        background: rgba(255,255,255,23); border: none; border-radius: 2px;
        max-height: 4px; min-height: 4px; text-align: center;
    }}
    QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}

    /* 调试面板：等宽字，贴底，默认不出现 */
    QFrame#debugPanel {{ background: rgba(0,0,0,110); border: 1px solid {BORDER}; border-radius: 6px; }}
    QLabel#debugText {{ color: #9AA3AD; font-family: "Consolas", monospace; font-size: 11px; }}

    /* 设置抽屉：必须**不透明**。半透明会把下面的卡片文字透出来，看着像两个界面叠在一起 */
    QFrame#drawer {{ background: #14171C; border: 1px solid {BORDER}; border-radius: {RADIUS}px; }}
    QFrame#drawer QLabel {{ font-size: {SZ_BODY}px; }}
    /* 设置抽屉的关闭键（v1.21）：26px 灰字「×」太不显眼，改成带底色的胶囊，
       hover 变红 —— 让它在视觉上真的像个"关掉这个面板"的按钮。 */
    QFrame#drawer QPushButton#drawerClose {{
        min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
        padding: 0; border-radius: 14px;
        background: rgba(255,255,255,14);
        border: 1px solid rgba(255,255,255,30);
    }}
    QFrame#drawer QPushButton#drawerClose:hover {{
        background: #C24A44; border-color: #D9615A;
    }}

    /* 设置分组（v1.19）：一张卡片一组，标题条整条可点。
       选择器带上 #section 的 id：不然上面 `QFrame#drawer QLabel` 的 id 数更少但
       元素数更多，会把标题字号顶掉（Qt 的特异性按 id 优先）。 */
    QFrame#section {{
        background: rgba(255,255,255,8);
        border: 1px solid rgba(255,255,255,28);
        border-radius: 10px;
    }}
    QFrame#section QWidget#secHead {{ background: transparent; border-radius: 10px; }}
    QFrame#section QWidget#secHead:hover {{ background: rgba(255,255,255,12); }}
    QFrame#section QLabel#secTitle {{
        color: {TEXT}; font-size: {SZ_BODY}px; font-weight: 600;
    }}
    QFrame#section QLabel#secSub {{
        color: {TEXT_DIM}; font-size: {SZ_SUB}px; font-weight: 400;
    }}

    /* 控件统一高度，勾选框与输入框视觉齐平 */
    QCheckBox {{ min-height: 24px; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; }}

    /* 输入框 */
    QLineEdit, QSpinBox, QComboBox {{
        background: rgba(255,255,255,16); border: 1px solid {BORDER};
        border-radius: 6px; padding: 0 8px; min-height: 28px; max-height: 28px;
    }}

    /* 滚动区：viewport 是 Qt 自己建的控件，默认填 palette 的 Base 色 ——
       Win10 浅色主题下那是纯白，而本项目的卡片/输入框全是半透明白，
       叠上去就是白板 + 浅灰字（设置抽屉整片看不见）。这里显式透明，
       代码里再关掉 viewport 的 autoFillBackground 兜一层。 */
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}

    /* 提示气泡 */
    QToolTip {{ background: #23262C; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px 6px; }}
    """


def dark_palette():
    """统一的深色调色板：程序外观不再跟系统主题走。

    为什么必须有这一层：Win10 浅色主题下，Qt **自己内部建**的控件 — QScrollArea
    的 viewport、下拉弹窗的列表、菜单、提示气泡 — 吃的是系统 palette 的 Base 色
    （= 白色）。本项目整套配色是 `rgba(255,255,255,N)` 叠在深色底上的半透明层，
    压到白底上就变成"白板 + 浅灰字"，设置抽屉整片看不见（2026-11-07 用户 Win10 实测）。

    QSS 只能盖住自己声明过的控件，盖不住这些内部控件；`Widget.setPalette()` 也不顶用
    （子控件仍继承系统色）。唯一稳的办法是在 QApplication 上一次性钉死。

    颜色与 QSS 同一来源，改令牌这里自动跟着变。
    """
    from PySide6.QtGui import QColor, QPalette as QP

    base = QColor("#14171C")       # 与 QFrame#drawer / 毛玻璃底一致
    surface = QColor("#1B1F25")
    p = QP()
    p.setColor(QP.Window, base)
    p.setColor(QP.WindowText, QColor(TEXT))
    p.setColor(QP.Base, base)
    p.setColor(QP.AlternateBase, surface)
    p.setColor(QP.Text, QColor(TEXT))
    p.setColor(QP.Button, surface)
    p.setColor(QP.ButtonText, QColor(TEXT))
    p.setColor(QP.BrightText, QColor("#FFFFFF"))
    p.setColor(QP.ToolTipBase, QColor("#23262C"))
    p.setColor(QP.ToolTipText, QColor(TEXT))
    p.setColor(QP.Highlight, QColor(ACCENT))
    p.setColor(QP.HighlightedText, QColor("#FFFFFF"))
    p.setColor(QP.Link, QColor("#7FB0FF"))
    p.setColor(QP.PlaceholderText, QColor(TEXT_DIM))
    p.setColor(QP.Light, QColor("#2A2F37"))
    p.setColor(QP.Midlight, QColor("#23272F"))
    p.setColor(QP.Mid, surface)
    p.setColor(QP.Dark, QColor("#0E1014"))
    p.setColor(QP.Shadow, QColor("#000000"))
    # 禁用态：不给的话禁用按钮和可用的长得一模一样
    for role in (QP.Text, QP.WindowText, QP.ButtonText):
        p.setColor(QP.Disabled, role, QColor("#5A6270"))
    return p
