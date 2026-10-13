"""
Windows 全局快捷键管理模块
使用 Windows 原生 RegisterHotKey API 实现稳定且低开销的系统级热键监听
"""

import ctypes
import logging
from ctypes import wintypes
from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

HOTKEY_ID_SNIP = 101          # F1: Snipaste 交互截图 (带工具栏)
HOTKEY_ID_SNIP_FAST = 102     # F4: 极速截屏直接翻译贴图
HOTKEY_ID_CLIPBOARD = 103     # F3: 剪贴板贴图


def parse_hotkey(hotkey_str: str):
    """解析热键字符串为 (modifiers, vk_code)"""
    if not hotkey_str:
        return None, None
    parts = [p.strip().upper() for p in hotkey_str.split('+')]
    mod = MOD_NOREPEAT
    vk = 0
    
    vk_map = {
        'F1': 0x70, 'F2': 0x71, 'F3': 0x72, 'F4': 0x73,
        'F5': 0x74, 'F6': 0x75, 'F7': 0x76, 'F8': 0x77,
        'F9': 0x78, 'F10': 0x79, 'F11': 0x7A, 'F12': 0x7B,
        'ESC': 0x1B, 'ESCAPE': 0x1B, 'SPACE': 0x20, 'TAB': 0x09,
        'RETURN': 0x0D, 'ENTER': 0x0D, 'BACKSPACE': 0x08,
        'PRINTSCREEN': 0x2C, 'PRINT': 0x2C, 'SNAPSHOT': 0x2C,
        'INSERT': 0x2D, 'DELETE': 0x2E, 'HOME': 0x24, 'END': 0x23,
        'PAGEUP': 0x21, 'PAGEDOWN': 0x22,
        'UP': 0x26, 'DOWN': 0x28, 'LEFT': 0x25, 'RIGHT': 0x27,
        '`': 0xC0, '~': 0xC0, '-': 0xBD, '=': 0xBB,
        '[': 0xDB, ']': 0xDD, '\\': 0xDC, ';': 0xBA,
        "'": 0xDE, ',': 0xBC, '.': 0xBE, '/': 0xBF,
    }

    for p in parts:
        if p in ('ALT', 'MENU'):
            mod |= MOD_ALT
        elif p in ('CTRL', 'CONTROL'):
            mod |= MOD_CONTROL
        elif p in ('SHIFT',):
            mod |= MOD_SHIFT
        elif p in ('WIN', 'WINDOWS'):
            mod |= MOD_WIN
        elif p in vk_map:
            vk = vk_map[p]
        elif len(p) == 1 and ('A' <= p <= 'Z' or '0' <= p <= '9'):
            vk = ord(p)
        elif p in vk_map:
            vk = vk_map[p]
            
    if vk == 0:
        return None, None
    return mod, vk


class GlobalHotkeyThread(QThread):
    snip_triggered = pyqtSignal()
    snip_fast_triggered = pyqtSignal()
    clipboard_triggered = pyqtSignal()
    error_signal = pyqtSignal(str)

    def __init__(self, snip_hotkey="F1", snip_fast_hotkey="F4", clipboard_hotkey="F3"):
        super().__init__()
        self.snip_hotkey_str = snip_hotkey
        self.snip_fast_hotkey_str = snip_fast_hotkey
        self.clipboard_hotkey_str = clipboard_hotkey
        self.thread_id = None
        self._running = False
        self._registered_ids = []

    def set_hotkeys(self, snip_hotkey="F1", snip_fast_hotkey="F4", clipboard_hotkey="F3"):
        self.snip_hotkey_str = snip_hotkey
        self.snip_fast_hotkey_str = snip_fast_hotkey
        self.clipboard_hotkey_str = clipboard_hotkey

    def run(self):
        self._running = True
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        self._registered_ids = []

        # 1. 注册标准截图热键 (如 F1)
        snip_mod, snip_vk = parse_hotkey(self.snip_hotkey_str)
        if snip_vk is not None:
            res1 = ctypes.windll.user32.RegisterHotKey(None, HOTKEY_ID_SNIP, snip_mod, snip_vk)
            if res1:
                self._registered_ids.append(HOTKEY_ID_SNIP)
                logger.info(f"Registered snip hotkey: {self.snip_hotkey_str}")
            else:
                err = f"注册截屏快捷键 [{self.snip_hotkey_str}] 失败，可能已被占用"
                logger.warning(err)
                self.error_signal.emit(err)

        # 2. 注册极速截屏翻译热键 (如 F4)
        fast_mod, fast_vk = parse_hotkey(self.snip_fast_hotkey_str)
        if fast_vk is not None:
            res2 = ctypes.windll.user32.RegisterHotKey(None, HOTKEY_ID_SNIP_FAST, fast_mod, fast_vk)
            if res2:
                self._registered_ids.append(HOTKEY_ID_SNIP_FAST)
                logger.info(f"Registered fast snip hotkey: {self.snip_fast_hotkey_str}")
            else:
                err = f"注册一键翻译快捷键 [{self.snip_fast_hotkey_str}] 失败，可能已被占用"
                logger.warning(err)
                self.error_signal.emit(err)

        # 3. 注册剪贴板贴图热键 (如 F3)
        clip_mod, clip_vk = parse_hotkey(self.clipboard_hotkey_str)
        if clip_vk is not None:
            res3 = ctypes.windll.user32.RegisterHotKey(None, HOTKEY_ID_CLIPBOARD, clip_mod, clip_vk)
            if res3:
                self._registered_ids.append(HOTKEY_ID_CLIPBOARD)
                logger.info(f"Registered clipboard hotkey: {self.clipboard_hotkey_str}")
            else:
                err = f"注册贴图快捷键 [{self.clipboard_hotkey_str}] 失败，可能已被占用"
                logger.warning(err)
                self.error_signal.emit(err)

        # Windows 原生消息循环（CPU 0.0%）
        msg = wintypes.MSG()
        while self._running:
            ret = ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0 or msg.message == WM_QUIT:
                break

            if msg.message == WM_HOTKEY:
                hotkey_id = msg.wParam
                logger.info(f"Hotkey triggered: id={hotkey_id}")
                if hotkey_id == HOTKEY_ID_SNIP:
                    self.snip_triggered.emit()
                elif hotkey_id == HOTKEY_ID_SNIP_FAST:
                    self.snip_fast_triggered.emit()
                elif hotkey_id == HOTKEY_ID_CLIPBOARD:
                    self.clipboard_triggered.emit()

        # 注销热键
        for hid in self._registered_ids:
            ctypes.windll.user32.UnregisterHotKey(None, hid)
        self._registered_ids.clear()
        logger.info("Global hotkey thread stopped cleanly.")

    def stop(self):
        self._running = False
        if self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
        self.wait(1000)
