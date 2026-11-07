# -*- coding: utf-8 -*-
"""版本与项目信息。改版本只改这里，关于窗口和标题徽标都读它。"""

VERSION = "1.36"
CHANNEL = "Beta"
BUILT = "2026-11-07"

APP_NAME = "嗷润吉·联盟采集"
DEVELOPER = "嗷润吉·1E-6制作"

# 参考与致谢：本项目的数据判据与交互参考了阿卡团队的练度采集工具
CREDIT_TITLE = "阿卡团队 · Arcana 练度采集"
CREDIT_URL = "https://pinkuro.top:9984/arcana/web?k="
CREDIT = ("本项目参考了阿卡团队（Arcana）的练度采集工具～"
          "感谢大佬们对 NIKKE 社区的贡献～")


def version_text() -> str:
    """`v1.12 Beta`"""
    return f"v{VERSION} {CHANNEL}".strip()


def full_text() -> str:
    """关于窗口标题下那行：`v1.12 Beta · 2026-11-07`"""
    return f"{version_text()} · {BUILT}"
