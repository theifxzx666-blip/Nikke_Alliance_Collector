# -*- coding: utf-8 -*-
"""全局 Esc 监听（v1.9）。

为什么需要它：Esc 做的是 `QShortcut(WindowShortcut)`，只在客户端有键盘焦点时生效；
而采集时必须让游戏在前台（滚轮注入、截图都要求），那一刻按键全进游戏，
客户端根本收不到 Esc —— 用户描述的"采集时 Esc 中断不了"就是这个。

为什么不注册成全局热键：`RegisterHotKey` 会**吃掉** Esc，游戏里所有用 Esc 关界面的
操作都会失效。这里改成被动轮询 `GetAsyncKeyState`：只读键状态，不拦截，
游戏照常响应 Esc，同时我们也能立刻停采集。

只在运行中启用（`set_active`），停止后马上关掉轮询，避免平时按 Esc 误触发。
"""
import ctypes
import sys

from PySide6.QtCore import QObject, QTimer, Signal

VK_ESCAPE = 0x1B


def _down() -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
    except Exception:
        return False


class EscWatch(QObject):
    """运行期被动监听 Esc；按下沿触发一次。"""

    pressed = Signal()

    def __init__(self, parent=None, interval: int = 120):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(int(interval))
        self._timer.timeout.connect(self._tick)
        self._was_down = False

    def set_active(self, on: bool):
        if on:
            # 起步时先记下当前状态：开始采集时若正按着 Esc，不该立刻被当成一次"按下"
            self._was_down = _down()
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._was_down = False

    def is_active(self) -> bool:
        return self._timer.isActive()

    def _tick(self):
        down = _down()
        if down and not self._was_down:
            self._was_down = True
            self.pressed.emit()
        elif not down:
            self._was_down = False
