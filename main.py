#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
主程序入口
"""

import sys
import os
import ctypes

def main():
    # 强制启用 Windows Per-Monitor v2 高分屏 DPI 真实感知，避免系统模糊拉伸与坐标虚拟化
    if sys.platform.startswith('win'):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtNetwork import QLocalServer, QLocalSocket
    from src.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    # 确保退出所有窗口时不直接退出应用，保持后台托盘驻留
    app.setQuitOnLastWindowClosed(False)

    server_name = "SnipasteOCR_SingleInstance_IPC"
    ipc_socket = QLocalSocket()
    ipc_socket.connectToServer(server_name)
    if ipc_socket.waitForConnected(300):
        # 已有实例在后台运行：向其投递截屏指令并立即退出当前冗余进程
        cmd = "show" if "--show" in sys.argv else ("fast" if "--fast" in sys.argv else "snip")
        ipc_socket.write(cmd.encode('utf-8'))
        ipc_socket.waitForBytesWritten(1000)
        ipc_socket.disconnectFromServer()
        sys.exit(0)

    # 首次启动：启动本地 IPC 服务
    ipc_server = QLocalServer()
    ipc_server.removeServer(server_name)
    ipc_server.listen(server_name)

    mainWindow = MainWindow()

    def handle_client_ipc():
        client = ipc_server.nextPendingConnection()
        if client:
            def on_ready_read():
                try:
                    data = client.readAll().data().decode('utf-8', errors='ignore').strip()
                    if data == "show":
                        mainWindow.showNormal()
                        mainWindow.activateWindow()
                    elif data == "fast":
                        mainWindow.start_fast_snip_and_pin()
                    elif data == "snip":
                        mainWindow.start_standard_snip()
                except Exception:
                    pass
            client.readyRead.connect(on_ready_read)
    ipc_server.newConnection.connect(handle_client_ipc)

    if '--show' in sys.argv:
        mainWindow.show()
    else:
        # 默认直接挂载到系统托盘后台运行
        mainWindow.hide()
        if hasattr(mainWindow, 'trayIcon') and mainWindow.trayIcon:
            snip_k = mainWindow.hotkeys.get('snip', 'F1')
            fast_k = mainWindow.hotkeys.get('snip_and_pin', 'F4')
            from PyQt6.QtWidgets import QSystemTrayIcon
            mainWindow.trayIcon.showMessage(
                "SnipasteOCR",
                f"已挂载到后台运行！\n截屏快捷键: {snip_k} | 极速翻译: {fast_k}\n双击右下角托盘图标可打开设置界面。",
                QSystemTrayIcon.MessageIcon.Information,
                2500
            )

    sys.exit(app.exec())

if __name__ == '__main__':
    main() 