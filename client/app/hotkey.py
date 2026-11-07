# -*- coding: utf-8 -*-
"""全局热键：焦点在游戏里也要能按。

为什么不能用 `QShortcut`
========================
`QShortcut` 默认是 `Qt.WindowShortcut` —— **只有本窗口有焦点时才响应**。
而用户 99% 的时间焦点在游戏里（对着游戏按 F4 是常态），
窗口内快捷键在那时等于不存在。实测反馈就是「快捷键点了没反应」。

这里走 Win32 的 `RegisterHotKey`：注册成系统级热键，系统在任何前台窗口下
都会往本线程的消息队列投一条 `WM_HOTKEY`，Qt 的事件分发器再交给 native filter。

两个刻意的决定
--------------
1. **用线程级注册（`hwnd=None`）**：本客户端会 `setWindowFlag(置顶)` 重建原生窗口，
   绑定 hwnd 的注册会在那一刻失效，而且失败得毫无声响。线程级没有这个问题。
2. **Esc 永远不注册**：全局劫持 Esc 会让游戏里的 Esc 失灵，是灾难级副作用。
   Esc 只保留窗口内语义。

v1.24 加了作用域（`set_scope`）
------------------------------
`RegisterHotKey` 是独占的：注册期间那个键在浏览器、聊天框里也收不到，还照样
触发本工具的采集。用户的要求是"只在游戏的客户端和工具的客户端里响应"，
所以由宿主每 300 ms 判一次前台窗口（`win.in_scope`），离开范围就注销、
回来再注册 —— 既不误触，也不抢占别的程序。
"""
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication

WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000          # 长按不连发，否则按住 F4 会反复 start/stop

# F1 = 0x70，F2 = 0x71 …（VK_F1..VK_F12）
VK_F = {f"F{i}": 0x6F + i for i in range(1, 13)}


class Hotkeys(QAbstractNativeEventFilter):
    """注册/注销全局热键，并把 `WM_HOTKEY` 转成回调。"""

    def __init__(self):
        super().__init__()
        self._binds = []          # [(键名, 回调)]，注册的"真源"，注销时才不会丢
        self._map = {}            # id -> (键名, 回调)，只在注册期间有效
        self._ok = []
        self._bad = []
        self._installed = False
        self._scope = True

    # ---------------- 对外 ----------------
    def install(self, binds):
        """binds: [(键名, 回调), …]，键名形如 "F4"。

        返回 (成功列表, 失败列表)。**失败的键要由调用方退回窗口内快捷键并写日志**，
        不许静默 —— 用户按了没反应又查不到原因，就是这一轮被骂的那件事。
        """
        self.uninstall()
        self._binds = [(n, f) for n, f in binds]
        return self._register_all()

    def uninstall(self):
        self._unregister_all()
        self._binds = []
        self._ok.clear()
        self._bad.clear()

    def set_scope(self, on: bool):
        """进入/离开作用域。返回这次切到"注册"时失败的键名。"""
        on = bool(on)
        if on == self._scope:
            return []
        self._scope = on
        if not on:
            self._unregister_all()
            return []
        _, bad = self._register_all()
        return bad

    def names(self):
        return list(self._ok)

    def scoped(self) -> bool:
        return self._scope

    # ---------------- 内部 ----------------
    def _unregister_all(self):
        for hid in list(self._map):
            _unregister(hid)
        self._map.clear()
        self._ok.clear()          # names() 要反映"此刻真正占着"的键，不是历史

    def _register_all(self):
        self._ok.clear()
        self._bad.clear()
        self._map.clear()
        if not self._binds or not self._scope:
            return list(self._ok), list(self._bad)
        if not self._installed:
            QCoreApplication.instance().installNativeEventFilter(self)
            self._installed = True
        for hid, (name, fn) in enumerate(self._binds, 1):
            vk = VK_F.get(name)
            if vk is None:
                self._bad.append(name)
                continue
            if _register(hid, vk):
                self._map[hid] = (name, fn)
                self._ok.append(name)
            else:
                self._bad.append(name)
        return list(self._ok), list(self._bad)

    # ---------------- 消息 ----------------
    def nativeEventFilter(self, event_type, message):
        try:
            if bytes(event_type) != b"windows_generic_MSG":
                return False
            msg = wintypes.MSG.from_address(int(message))
            if msg.message != WM_HOTKEY:
                return False
            item = self._map.get(int(msg.wParam))
            if item is None:
                return False
            item[1]()
            return True           # 已消费：别让 Qt 再当普通按键分发给控件
        except Exception:
            return False


def _register(hid, vk) -> bool:
    try:
        import ctypes
        # MOD_NOREPEAT 在 Vista+ 才有；老系统会返回失败，那就退回不带它再试一次
        if ctypes.windll.user32.RegisterHotKey(None, hid, MOD_NOREPEAT, vk):
            return True
        return bool(ctypes.windll.user32.RegisterHotKey(None, hid, 0, vk))
    except Exception:
        return False


def _unregister(hid) -> None:
    try:
        import ctypes
        ctypes.windll.user32.UnregisterHotKey(None, hid)
    except Exception:
        pass
