"""
Manual check: enter an alternate screen ourselves, then run tail | less -X in
it with colored text, like watch mode on the kill_less_fix branch. Look at
whether "RED" is red, then press q; the original screen should come back.

Usage: python probes/manual_alt_screen.py [vt|buffer]
    vt      ESC[?1049h / ESC[?1049l (default)
    buffer  CreateConsoleScreenBuffer + SetConsoleActiveScreenBuffer
"""

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LESS = os.path.join(HARNESS, "less.exe")
TEXT = "".join(
    f"line {i}  \x1b[31mRED\x1b[0m  \x1b[32mGREEN\x1b[0m  \x1b[1;34mBOLD BLUE\x1b[0m\n"
    for i in range(200)
)

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.GetStdHandle.restype = wintypes.HANDLE
k32.CreateConsoleScreenBuffer.restype = wintypes.HANDLE
k32.CreateConsoleScreenBuffer.argtypes = [wintypes.DWORD] * 2 + [wintypes.LPVOID] + [wintypes.DWORD, wintypes.LPVOID]
k32.SetConsoleActiveScreenBuffer.argtypes = [wintypes.HANDLE]
k32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
k32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]


def main() -> None:
    how = sys.argv[1] if len(sys.argv) > 1 else "vt"
    out = k32.GetStdHandle(-11 & 0xFFFFFFFF)
    mode = wintypes.DWORD()
    k32.GetConsoleMode(out, ctypes.byref(mode))
    print(f"[original screen] about to enter the alt screen via {how}")
    input("press Enter...")

    alt = None
    if how == "vt":
        k32.SetConsoleMode(out, mode.value | 0x4)  # VT processing
        sys.stdout.write("\x1b[?1049h")
        sys.stdout.flush()
    else:
        alt = k32.CreateConsoleScreenBuffer(0xC0000000, 3, None, 1, None)
        k32.SetConsoleMode(alt, mode.value | 0x4)
        k32.SetConsoleActiveScreenBuffer(alt)

    env = {k: v for k, v in os.environ.items() if k not in ("LESS", "LESSOPEN")}
    buf = subprocess.Popen(["tail", "-c", str(10**9)], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    less = subprocess.Popen([LESS, "-SRic", "-+F", "-X", "-#6", "-K"], stdin=buf.stdout, env=env)
    assert buf.stdin and buf.stdout
    buf.stdout.close()
    buf.stdin.write(TEXT.encode())
    buf.stdin.close()
    less.wait()
    buf.wait()

    if how == "vt":
        sys.stdout.write("\x1b[?1049l")
        sys.stdout.flush()
        k32.SetConsoleMode(out, mode.value)
    else:
        k32.SetConsoleActiveScreenBuffer(out)
        k32.CloseHandle(alt)
    print(f"[original screen] back from {how}; less exited with {less.returncode}")


if __name__ == "__main__":
    main()
