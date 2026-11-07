# -*- coding: utf-8 -*-
"""成员库 / BOSS 库的管理对话框（Qt，不碰 Tk）。

对应客户端设计-v1.md §13.2：列表 + 新建/另存/重命名/删除；BOSS 库多一个
5 行编辑区（一期固定 5 个 BOSS）。**所有落盘都走 booklib**，这里只管交互；
错误（空名、非法字符、重名、删活动套）一律就地弹提示，不关闭、不静默改字。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QInputDialog,
                               QLabel, QListWidget, QListWidgetItem, QMessageBox,
                               QPlainTextEdit, QPushButton, QVBoxLayout)

import booklib as BK

from . import paths as P
from . import theme as T


class BookDialog(QDialog):
    """kind = "roster"（成员库）或 "boss"（BOSS 库）。"""

    def __init__(self, parent=None, kind="roster"):
        super().__init__(parent)
        self.kind = kind
        self.changed = False                 # 关闭后 shell 据此重载下拉
        self.setWindowTitle("成员库管理" if kind == "roster" else "BOSS 库管理")
        self.setMinimumWidth(560)
        self.setStyleSheet(T.qss())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        tip = QLabel("双击一行设为当前库；删库前会再问一次。" if kind == "roster"
                     else "选一行后在下面改 BOSS 名（一行一个，通常 5 个），再点【保存编辑】。")
        tip.setProperty("role", "sub")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self.list = QListWidget()
        self.list.setMinimumHeight(190)
        self.list.itemDoubleClicked.connect(self._activate)
        lay.addWidget(self.list)

        self.editor = None
        if kind == "boss":
            self.editor = QPlainTextEdit()
            self.editor.setPlaceholderText("一行一个 BOSS 名，例如：\n邪魔\n医师\n铁匠\n重装火神R\n古铁")
            self.editor.setFixedHeight(110)
            lay.addWidget(self.editor)

        row = QHBoxLayout()
        row.setSpacing(8)
        for text, fn in (("新建…", self._create),
                         ("另存当前…", self._save_as),
                         ("重命名…", self._rename),
                         ("删除", self._delete)):
            b = QPushButton(text)
            b.setProperty("role", "sub")
            b.clicked.connect(fn)
            row.addWidget(b)
        if kind == "boss":
            b = QPushButton("保存编辑")
            b.clicked.connect(self._save_edit)
            row.addWidget(b)
        else:
            # v1.31：手工补名单的回路。导出的 CSV 以前只是快照，用户改完等于白改。
            b = QPushButton("导入 CSV…")
            b.setToolTip("把成员名单 CSV 里的昵称并进选中的库（只增不删）。\n"
                         "导出一份 → 在 Excel 里补几个真名 → 存成 CSV（GBK 或 UTF-8 都行）→ 从这里导回来")
            b.setProperty("role", "sub")
            b.clicked.connect(self._import_csv)
            row.addWidget(b)
        row.addStretch(1)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)

        self.list.currentItemChanged.connect(self._on_pick)
        self._refresh()

    # ---------------- 数据

    def _entries(self):
        return BK.list_rosters() if self.kind == "roster" else BK.list_bosses()

    def _refresh(self, keep=None):
        keep = keep or self._selected()
        self.list.blockSignals(True)
        self.list.clear()
        for e in self._entries():
            n = e["people"] if self.kind == "roster" else e["count"]
            unit = "人" if self.kind == "roster" else "个"
            mark = "● " if e["active"] else "   "
            it = QListWidgetItem(f"{mark}{e['name']}    {n} {unit}    {e['updated'] or '—'}")
            it.setData(Qt.UserRole, e["name"])
            self.list.addItem(it)
            if keep and e["name"] == keep:
                self.list.setCurrentItem(it)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        self.list.blockSignals(False)
        self._on_pick()

    def _selected(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else ""

    def _on_pick(self, *_a):
        if self.editor is None:
            return
        name = self._selected()
        if not name:
            self.editor.clear()
            return
        bs = BK.read_boss(name).get("bosses") or []
        self.editor.setPlainText("\n".join(bs))

    # ---------------- 动作

    def _activate(self, _item=None):
        name = self._selected()
        if not name:
            return
        ok = (BK.set_active_roster(name) if self.kind == "roster"
              else BK.set_active_boss(name))
        if not ok:
            QMessageBox.warning(self, "切不过去", f"「{name}」不存在或索引损坏")
            return
        self.changed = True
        self._refresh(name)

    def _create(self):
        name, ok = QInputDialog.getText(
            self, "新建", "库名（例如：嗷润吉-Q区）：" if self.kind == "roster"
            else "库名（例如：S19 国服）：")
        if not ok:
            return
        n, err = BK.valid_name(name)
        if err:
            QMessageBox.warning(self, "名字不行", err)
            return
        if self.kind == "roster":
            if BK.write_roster(n, {"version": 1, "rounds": 0, "names": {}}):
                self.changed = True
                self._refresh(n)
            else:
                QMessageBox.warning(self, "建不了", "写入失败（目录权限？）")
            return
        bs = [s.strip() for s in self.editor.toPlainText().splitlines() if s.strip()] \
            if self.editor else []
        if not bs:
            QMessageBox.warning(self, "先填 BOSS", "在下面编辑区一行一个写上 BOSS 名")
            return
        if BK.write_boss(n, bs):
            self.changed = True
            self._refresh(n)
        else:
            QMessageBox.warning(self, "建不了", "写入失败（目录权限？）")

    def _save_as(self):
        name, ok = QInputDialog.getText(self, "另存当前", "新库名：")
        if not ok:
            return
        if self.kind == "roster":
            okk, msg = BK.save_roster_as(name)
        else:
            bs = [s.strip() for s in self.editor.toPlainText().splitlines() if s.strip()]
            okk, msg = BK.save_boss_as(name, bs or None)
        if not okk:
            QMessageBox.warning(self, "存不了", msg)
            return
        self.changed = True
        self._refresh(name.strip())

    def _rename(self):
        old = self._selected()
        if not old:
            return
        name, ok = QInputDialog.getText(self, "重命名", f"「{old}」改成：", text=old)
        if not ok or name.strip() == old:
            return
        okk, msg = (BK.rename_roster(old, name) if self.kind == "roster"
                    else BK.rename_boss(old, name))
        if not okk:
            QMessageBox.warning(self, "改不了", msg)
            return
        self.changed = True
        self._refresh(name.strip())

    def _delete(self):
        name = self._selected()
        if not name:
            return
        if QMessageBox.question(self, "删除库", f"确定删除「{name}」？\n删了就找不回来了。"
                               ) != QMessageBox.Yes:
            return
        okk, msg = (BK.delete_roster(name) if self.kind == "roster"
                    else BK.delete_boss(name))
        if not okk:
            QMessageBox.warning(self, "删不了", msg)
            return
        self.changed = True
        self._refresh()

    def _import_csv(self):
        """把导出的名单 CSV（手改过）并回选中的库。

        以前导出的 CSV 只是快照：用户补上漏掉的真名，工具根本不回读，
        改完等于白改。这里只补人，不删人 —— 导入是用来补漏的，不是用来覆盖的。
        """
        name = self._selected()
        if not name:
            QMessageBox.information(self, "先选一个库",
                                    "在上面选一套库，导入的名单会并进它（不会删掉已有的）")
            return
        start = str(P.sub(P.ROSTER))
        p, _ = QFileDialog.getOpenFileName(
            self, f"导入到「{name}」", start, "CSV 文件 (*.csv);;所有文件 (*)")
        if not p:
            return
        try:
            added, exist, skip, err = BK.import_roster_csv(p, name)
        except Exception as e:
            QMessageBox.warning(self, "导入失败", f"{e}")
            return
        if err:
            QMessageBox.warning(self, "导入失败", err)
            return
        if not added:
            QMessageBox.information(
                self, "没有新增",
                f"表里的 {exist} 个人库里都已经有了，跳过 {skip} 行（空行 / 纯数字）。")
            return
        self.changed = True
        self._refresh(name)
        total = len((BK.read_roster(name).get("names") or {}))
        QMessageBox.information(
            self, "导入完成",
            f"新增 {added} 人 · 已有 {exist} 人 · 跳过 {skip} 行\n"
            f"「{name}」现在 {total} 人。")

    def _save_edit(self):
        name = self._selected()
        if not name or self.editor is None:
            return
        bs = [s.strip() for s in self.editor.toPlainText().splitlines() if s.strip()]
        if not bs:
            QMessageBox.warning(self, "空的", "至少留一个 BOSS 名")
            return
        if not BK.write_boss(name, bs, activate=(BK.active_boss() == name)):
            QMessageBox.warning(self, "写不了", "写入失败（目录权限？）")
            return
        self.changed = True
        self._refresh(name)
