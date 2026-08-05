"""子进程守护：父进程退出后自动结束被守护进程，防止遗留孤儿进程。

原理：用 WaitForSingleObject 阻塞等待父进程句柄；父进程无论正常退出、
崩溃还是被强杀，其进程句柄都会变为"已信号"状态，守护随即结束子进程
并退出。该机制不依赖 Job Object，因此在受限/沙箱环境下同样有效。

用法（由 TTS/ASR 内部拉起，不面向用户）：
    python child_watchdog.py --parent <父进程PID> --child <被守护进程PID>
"""

import argparse
import ctypes
import os
import signal
import sys
from ctypes import wintypes


def _win():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel32


def _parent_handle(pid: int):
    """以 SYNCHRONIZE 权限打开父进程句柄；父进程不存在时返回 0。"""
    kernel32 = _win()
    SYNCHRONIZE = 0x00100000
    return kernel32.OpenProcess(SYNCHRONIZE, False, pid)


def _terminate(pid: int) -> bool:
    """结束指定进程。优先 os.kill（走 _winapi 的 TerminateProcess 正确路径），
    失败再退回 ctypes 直接调用。"""
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except (OSError, PermissionError):
        pass
    try:
        kernel32 = _win()
        PROCESS_TERMINATE = 0x0400
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if not handle:
            return False
        try:
            return bool(kernel32.TerminateProcess(handle, 1))
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="child process watchdog")
    parser.add_argument("--parent", type=int, required=True)
    parser.add_argument("--child", type=int, required=True)
    args = parser.parse_args()

    kernel32 = _win()
    handle = _parent_handle(args.parent)
    if not handle:
        # 父进程已经不存在，直接清理子进程
        _terminate(args.child)
        return 0
    try:
        kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)  # INFINITE
    finally:
        kernel32.CloseHandle(handle)
    _terminate(args.child)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
