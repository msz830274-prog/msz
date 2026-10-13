"""
Snipaste 风格快捷键录制组件 (HotkeyEdit)
支持用户按下任意按键/组合键（如 Ctrl+Alt+A、F1、Alt+Q 等）直接自动录制与格式化
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLineEdit


class HotkeyEdit(QLineEdit):
    """
    自定义组合快捷键录制框
    - 点击后进入录制状态，按下键盘上的任意单键或组合键自动生成标准化快捷键字符串
    - 按 Backspace 或 Delete 可一键清空
    """
    hotkeyChanged = pyqtSignal(str)

    def __init__(self, current_hotkey="F1", parent=None):
        super().__init__(parent)
        self.setText(current_hotkey)
        self.setReadOnly(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setPlaceholderText("点击后按下键盘按键录制...")
        self.setStyleSheet("""
            QLineEdit {
                background-color: #2b2b2b;
                color: #2ecc71;
                font-weight: bold;
                font-size: 13px;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 4px 10px;
                min-width: 140px;
            }
            QLineEdit:focus {
                border: 2px solid #00a67d;
                background-color: #1a1a1a;
                color: #2ecc71;
            }
        """)

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()

        # 允许使用 Backspace 或 Delete 清空快捷键
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            self.setText("")
            self.hotkeyChanged.emit("")
            return

        # 如果单独按下了修饰键（Ctrl/Alt/Shift/Win），等待后续主键按下
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            return

        # 忽略 Escape 键（防止误设）
        if key == Qt.Key.Key_Escape:
            self.clearFocus()
            return

        parts = []
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append("Ctrl")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append("Alt")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append("Shift")
        if modifiers & Qt.KeyboardModifier.MetaModifier:
            parts.append("Win")

        key_str = self._key_to_string(key)
        if not key_str:
            return

        parts.append(key_str)
        hotkey_str = "+".join(parts)

        self.setText(hotkey_str)
        self.hotkeyChanged.emit(hotkey_str)
        self.clearFocus()

    def _key_to_string(self, key):
        """将 Qt 键码转换为标准化文本"""
        # 功能键 F1 ~ F12
        if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F12:
            return f"F{key - Qt.Key.Key_F1 + 1}"
        # 字母键 A ~ Z
        if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            return chr(key)
        # 数字键 0 ~ 9
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            return chr(key)
        
        # 常见按键映射
        key_map = {
            Qt.Key.Key_Space: "Space",
            Qt.Key.Key_Tab: "Tab",
            Qt.Key.Key_Return: "Enter",
            Qt.Key.Key_Enter: "Enter",
            Qt.Key.Key_Print: "PrintScreen",
            Qt.Key.Key_Insert: "Insert",
            Qt.Key.Key_Home: "Home",
            Qt.Key.Key_End: "End",
            Qt.Key.Key_PageUp: "PageUp",
            Qt.Key.Key_PageDown: "PageDown",
            Qt.Key.Key_QuoteLeft: "`",
            Qt.Key.Key_Minus: "-",
            Qt.Key.Key_Equal: "=",
            Qt.Key.Key_BracketLeft: "[",
            Qt.Key.Key_BracketRight: "]",
            Qt.Key.Key_Backslash: "\\",
            Qt.Key.Key_Semicolon: ";",
            Qt.Key.Key_Apostrophe: "'",
            Qt.Key.Key_Comma: ",",
            Qt.Key.Key_Period: ".",
            Qt.Key.Key_Slash: "/",
        }
        return key_map.get(key, "")
