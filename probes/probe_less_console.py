"""
Probe how less.exe behaves in a real Windows console, for the things the
kill_less_fix branch relies on with less on POSIX:

  - -+X: less switches to an alternate screen and restores the original one
    on exit; -X: it draws on the current screen and leaves no switch behind
  - -R: color escapes are rendered as colors, not shown raw
  - how less can be stopped, and whether each way restores the console
    (input/output modes = the "stty echo" equivalent, cursor, screen):
      q             keypress, the normal exit
      terminate     Popen.terminate() = TerminateProcess, the Windows
                    equivalent of the old kill(); less can't clean up
      ctrl_c_key    Ctrl-C delivered as a keypress, what a user's Ctrl-C
                    becomes if less turns off ENABLE_PROCESSED_INPUT
      ctrl_c_event  GenerateConsoleCtrlEvent(CTRL_C_EVENT) to the console
      ctrl_break    CTRL_BREAK_EVENT to less alone (needs its own process
                    group), the closest thing to sending it SIGTERM
      refresh       like watch mode: ctrl_break, start a second less, q
    each with and without -K (quit on interrupt)
  - watch mode: diff.py enters the alternate screen itself, then runs less -X
    in it ("prealt" scenarios, via VT ESC[?1049h or a new console screen
    buffer); less must draw there and leave it alone when it exits

Each scenario runs in its own new console (hidden unless --show), so your
terminal isn't touched. The child writes its findings to a JSON file and this
script prints a summary.

Usage: python probes/probe_less_console.py [--less path] [--show] [scenario...]
"""

import argparse
import ctypes
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from typing import Any, Callable, Dict, List, Optional

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# mirrors diff.py on the kill_less_fix branch
BUFFER_CMD = ["tail", "-c", str(10**9)]
ALT = ["-SRic", "-+F", "-+X", "-#6"]  # run_sync
NO_ALT = ["-SRic", "-+F", "-X", "-#6"]  # watch mode

# name -> (less flags, how to stop it[, how we enter the alt screen first])
SCENARIOS = {
    "alt_q": (ALT, "q"),
    "noalt_q": (NO_ALT, "q"),
    "alt_terminate": (ALT, "terminate"),
    "noalt_terminate": (NO_ALT, "terminate"),
    "alt_ctrl_c_key": (ALT, "ctrl_c_key"),
    "alt_K_ctrl_c_key": (ALT + ["-K"], "ctrl_c_key"),
    "alt_ctrl_c_event": (ALT, "ctrl_c_event"),
    "alt_K_ctrl_c_event": (ALT + ["-K"], "ctrl_c_event"),
    "alt_ctrl_break": (ALT, "ctrl_break"),
    "alt_K_ctrl_break": (ALT + ["-K"], "ctrl_break"),
    "noalt_K_ctrl_break": (NO_ALT + ["-K"], "ctrl_break"),
    "prealt_vt_noalt_q": (NO_ALT, "q", "vt"),
    "prealt_vt_noalt_K_refresh": (NO_ALT + ["-K"], "refresh", "vt"),
    "prealt_buffer_noalt_q": (NO_ALT, "q", "buffer"),
    "prealt_buffer_noalt_K_refresh": (NO_ALT + ["-K"], "refresh", "buffer"),
    # not an alt screen: only turns on VT processing before less starts
    "vtmode_noalt_q": (NO_ALT, "q", "vtmode"),
}

ORIGINAL_MARKER = "PROBE-ORIGINAL-SCREEN"
CONTENT_MARKER = "PROBE-CONTENT"
CONTENT = (
    CONTENT_MARKER
    + "\n"
    + "".join(f"line {i} \x1b[31mRED\x1b[0m end\n" for i in range(200))
)

# === console API ===

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
KEY_EVENT = 1
LEFT_CTRL_PRESSED = 0x8

IN_MODES = {
    0x1: "PROCESSED_INPUT",
    0x2: "LINE_INPUT",
    0x4: "ECHO_INPUT",
    0x8: "WINDOW_INPUT",
    0x10: "MOUSE_INPUT",
    0x20: "INSERT_MODE",
    0x40: "QUICK_EDIT",
    0x80: "EXTENDED_FLAGS",
    0x200: "VIRTUAL_TERMINAL_INPUT",
}
OUT_MODES = {
    0x1: "PROCESSED_OUTPUT",
    0x2: "WRAP_AT_EOL",
    0x4: "VIRTUAL_TERMINAL_PROCESSING",
    0x8: "DISABLE_NEWLINE_AUTO_RETURN",
    0x10: "LVB_GRID_WORLDWIDE",
}


class COORD(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [(n, wintypes.SHORT) for n in ["Left", "Top", "Right", "Bottom"]]


class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [
        ("dwSize", COORD),
        ("dwCursorPosition", COORD),
        ("wAttributes", wintypes.WORD),
        ("srWindow", SMALL_RECT),
        ("dwMaximumWindowSize", COORD),
    ]


class CONSOLE_CURSOR_INFO(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("bVisible", wintypes.BOOL)]


class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [
        ("bKeyDown", wintypes.BOOL),
        ("wRepeatCount", wintypes.WORD),
        ("wVirtualKeyCode", wintypes.WORD),
        ("wVirtualScanCode", wintypes.WORD),
        ("uChar", wintypes.WCHAR),
        ("dwControlKeyState", wintypes.DWORD),
    ]


class INPUT_RECORD(ctypes.Structure):
    # KEY_EVENT_RECORD is as big as the largest member of the real union
    _fields_ = [("EventType", wintypes.WORD), ("Event", KEY_EVENT_RECORD)]


kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetConsoleScreenBufferInfo.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(CONSOLE_SCREEN_BUFFER_INFO),
]
kernel32.GetConsoleCursorInfo.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(CONSOLE_CURSOR_INFO),
]
kernel32.ReadConsoleOutputCharacterW.argtypes = [
    wintypes.HANDLE,
    wintypes.LPWSTR,
    wintypes.DWORD,
    COORD,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.ReadConsoleOutputAttribute.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.WORD),
    wintypes.DWORD,
    COORD,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.WriteConsoleInputW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(INPUT_RECORD),
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.GenerateConsoleCtrlEvent.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.SetConsoleCtrlHandler.argtypes = [wintypes.LPVOID, wintypes.BOOL]
kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.CreateConsoleScreenBuffer.restype = wintypes.HANDLE
kernel32.CreateConsoleScreenBuffer.argtypes = [
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.LPVOID,
]
kernel32.SetConsoleActiveScreenBuffer.argtypes = [wintypes.HANDLE]


def check(ok: Any, what: str) -> None:
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error(), what)


def open_con(name: str) -> int:
    """CONIN$, or CONOUT$ = whatever screen buffer is active right now."""
    h = kernel32.CreateFileW(
        name,
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    check(h != INVALID_HANDLE_VALUE, f"open {name}")
    return h


def get_mode(h: int) -> int:
    mode = wintypes.DWORD()
    check(kernel32.GetConsoleMode(h, ctypes.byref(mode)), "GetConsoleMode")
    return mode.value


def mode_names(mode: int, names: Dict[int, str]) -> List[str]:
    return [n for bit, n in names.items() if mode & bit]


def screen_rows(h: int) -> List[str]:
    """Text of the visible window of a screen buffer."""
    info = CONSOLE_SCREEN_BUFFER_INFO()
    check(kernel32.GetConsoleScreenBufferInfo(h, ctypes.byref(info)), "GetCSBI")
    width = info.dwSize.X
    rows = []
    for y in range(info.srWindow.Top, info.srWindow.Bottom + 1):
        buf = ctypes.create_unicode_buffer(width)
        n = wintypes.DWORD()
        check(
            kernel32.ReadConsoleOutputCharacterW(
                h, buf, width, COORD(0, y), ctypes.byref(n)
            ),
            "ReadConsoleOutputCharacter",
        )
        rows.append(buf.value[: n.value].rstrip())
    return rows


def attr_at(h: int, x: int, y: int) -> int:
    """Attribute of a cell, y relative to the top of the visible window."""
    info = CONSOLE_SCREEN_BUFFER_INFO()
    check(kernel32.GetConsoleScreenBufferInfo(h, ctypes.byref(info)), "GetCSBI")
    attr = wintypes.WORD()
    n = wintypes.DWORD()
    pos = COORD(x, info.srWindow.Top + y)
    check(
        kernel32.ReadConsoleOutputAttribute(h, ctypes.byref(attr), 1, pos, ctypes.byref(n)),
        "ReadConsoleOutputAttribute",
    )
    return attr.value


def cursor_visible(h: int) -> bool:
    info = CONSOLE_CURSOR_INFO()
    check(kernel32.GetConsoleCursorInfo(h, ctypes.byref(info)), "GetCursorInfo")
    return bool(info.bVisible)


def send_key(conin: int, char: str, vk: int, ctrl: bool = False) -> None:
    """Put a key press + release into the console input buffer."""
    scan = user32.MapVirtualKeyW(vk, 0)
    records = (INPUT_RECORD * 2)()
    for rec, down in zip(records, [True, False]):
        rec.EventType = KEY_EVENT
        rec.Event = KEY_EVENT_RECORD(
            down, 1, vk, scan, char, LEFT_CTRL_PRESSED if ctrl else 0
        )
    n = wintypes.DWORD()
    check(kernel32.WriteConsoleInputW(conin, records, 2, ctypes.byref(n)), "WriteConsoleInput")


def wait_until(cond: Callable[[], bool], timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


# === child: runs inside the new console ===


def snapshot(conin: int, orig_out: int) -> Dict[str, Any]:
    """State of the console, as seen through the currently active buffer."""
    active = open_con("CONOUT$")
    try:
        rows = screen_rows(active)
        text = "\n".join(rows)
        snap: Dict[str, Any] = {
            "in_mode": mode_names(get_mode(conin), IN_MODES),
            "out_mode": mode_names(get_mode(active), OUT_MODES),
            "cursor_visible": cursor_visible(active),
            "shows_original": ORIGINAL_MARKER in text,
            "shows_content": CONTENT_MARKER in text,
            "orig_buffer_shows_content": CONTENT_MARKER in "\n".join(screen_rows(orig_out)),
            "raw_escapes": "[31m" in text or "\x1b" in text or "ESC" in text,
        }
        for y, row in enumerate(rows):
            x = row.find("RED")
            if x >= 0:
                # FOREGROUND_RED=4, FOREGROUND_INTENSITY=8; no blue/green
                snap["red_attr"] = hex(attr_at(active, x, y))
                snap["red_rendered"] = (attr_at(active, x, y) & 0x7) == 0x4
                break
        return snap
    finally:
        kernel32.CloseHandle(active)


def start_less(less_path: str, flags: List[str], new_group: bool) -> Any:
    """tail | less, like Display.run_less. Returns (buffer_proc, less_proc)."""
    env = dict(os.environ)
    for var in ["LESS", "LESSOPEN", "LESSCLOSE"]:
        env.pop(var, None)
    buffer_proc = subprocess.Popen(
        BUFFER_CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE
    )
    less_proc = subprocess.Popen(
        [less_path, *flags],
        stdin=buffer_proc.stdout,
        env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if new_group else 0,
    )
    assert buffer_proc.stdin and buffer_proc.stdout
    buffer_proc.stdout.close()
    buffer_proc.stdin.write(CONTENT.encode())
    buffer_proc.stdin.close()
    return buffer_proc, less_proc


def drawn() -> bool:
    h = open_con("CONOUT$")
    try:
        return CONTENT_MARKER in "\n".join(screen_rows(h))
    finally:
        kernel32.CloseHandle(h)


def run_child(name: str, less_path: str, out_path: str) -> None:
    flags, action, *rest = SCENARIOS[name]
    prealt = rest[0] if rest else None
    result: Dict[str, Any] = {"flags": flags, "action": action, "prealt": prealt}
    # whoever started the driver may have Ctrl-C ignored, which is inherited;
    # turn it back on for us and less
    kernel32.SetConsoleCtrlHandler(None, False)
    # ctrl_c_event goes to every process on the console, including us
    result["got_sigint"] = False

    def on_sigint(*_: object) -> None:
        result["got_sigint"] = True

    signal.signal(signal.SIGINT, on_sigint)
    try:
        conin = open_con("CONIN$")
        orig_out = open_con("CONOUT$")
        print(ORIGINAL_MARKER, flush=True)
        result["before"] = snapshot(conin, orig_out)

        alt_buffer = None
        if prealt in ("vt", "vtmode"):
            kernel32.SetConsoleMode(orig_out, get_mode(orig_out) | 0x4)
        if prealt == "vt":
            sys.stdout.write("\x1b[?1049h")
            sys.stdout.flush()
        elif prealt == "buffer":
            alt_buffer = kernel32.CreateConsoleScreenBuffer(
                GENERIC_READ | GENERIC_WRITE,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None,
                1,  # CONSOLE_TEXTMODE_BUFFER
                None,
            )
            check(alt_buffer != INVALID_HANDLE_VALUE, "CreateConsoleScreenBuffer")
            check(kernel32.SetConsoleActiveScreenBuffer(alt_buffer), "SetActive")
        if prealt:
            result["prealt_entered"] = snapshot(conin, orig_out)

        new_group = action in ("ctrl_break", "refresh")
        buffer_proc, less_proc = start_less(less_path, flags, new_group)
        result["drew"] = wait_until(drawn, 5)
        time.sleep(0.3)  # let less settle into waiting for keys
        result["during"] = snapshot(conin, orig_out)

        if action == "refresh":
            less_proc.send_signal(signal.CTRL_BREAK_EVENT)
            result["refresh_first_rc"] = less_proc.wait(timeout=3)
            buffer_proc.wait(timeout=3)
            # what's on screen between the two less instances
            result["between"] = snapshot(conin, orig_out)
            buffer_proc, less_proc = start_less(less_path, flags, new_group)
            result["refresh_drew"] = wait_until(drawn, 5)
            time.sleep(0.3)
            send_key(conin, "q", ord("Q"))
        elif action == "q":
            send_key(conin, "q", ord("Q"))
        elif action == "terminate":
            less_proc.terminate()
        elif action == "ctrl_c_key":
            send_key(conin, "\x03", ord("C"), ctrl=True)
        elif action == "ctrl_c_event":
            kernel32.GenerateConsoleCtrlEvent(signal.CTRL_C_EVENT, 0)
        elif action == "ctrl_break":
            less_proc.send_signal(signal.CTRL_BREAK_EVENT)

        try:
            result["returncode"] = less_proc.wait(timeout=3)
            result["exited"] = True
        except subprocess.TimeoutExpired:
            result["exited"] = False
            # still running: grab what it looks like, then quit it nicely
            time.sleep(0.2)
            result["still_running"] = snapshot(conin, orig_out)
            send_key(conin, "q", ord("Q"))
            try:
                less_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                less_proc.kill()
                less_proc.wait()
        try:
            buffer_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            buffer_proc.kill()
        time.sleep(0.2)
        if not result["exited"]:
            result["after_q"] = snapshot(conin, orig_out)
        else:
            result["after"] = snapshot(conin, orig_out)

        if prealt == "vt":
            sys.stdout.write("\x1b[?1049l")
            sys.stdout.flush()
        elif prealt == "buffer":
            kernel32.SetConsoleActiveScreenBuffer(orig_out)
            kernel32.CloseHandle(alt_buffer)
        if prealt:
            time.sleep(0.1)
            result["after_leave"] = snapshot(conin, orig_out)
    except BaseException as e:
        result["error"] = repr(e)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)


# === driver ===


def diff_modes(before: Dict[str, Any], after: Dict[str, Any], key: str) -> str:
    lost = sorted(set(before[key]) - set(after[key]))
    gained = sorted(set(after[key]) - set(before[key]))
    parts = [f"-{m}" for m in lost] + [f"+{m}" for m in gained]
    return " ".join(parts) or "same"


def report(name: str, r: Dict[str, Any]) -> None:
    print(f"== {name}: less {' '.join(r['flags'])}, stop with {r['action']}")
    if "error" in r:
        print(f"   ERROR: {r['error']}")
        return
    before = r["before"]
    d = r["during"]
    if r["prealt"] == "vtmode":
        print("   turned on VT processing first (no alt screen)")
    elif r["prealt"]:
        e = r["prealt_entered"]
        print(
            f"   entered alt screen via {r['prealt']} first:"
            f" original visible={e['shows_original']}"
        )
    print(f"   drew: {r['drew']}")
    print(
        "   during:"
        f" separate buffer={not d['orig_buffer_shows_content']}"
        f" original visible={d['shows_original']}"
        f" red rendered={d.get('red_rendered')} ({d.get('red_attr')})"
        f" raw escapes={d['raw_escapes']}"
    )
    print(f"           in mode vs before: {diff_modes(before, d, 'in_mode')}")
    print(f"           out mode vs before: {diff_modes(before, d, 'out_mode')}")
    if r["action"] == "refresh":
        b = r["between"]
        print(
            f"   refresh: first less rc={r['refresh_first_rc']};"
            f" between: original visible={b['shows_original']}"
            f" old content still visible={b['shows_content']};"
            f" second less drew={r['refresh_drew']}"
        )
    if r["action"] == "ctrl_c_event":
        print(f"   our python got SIGINT too: {r['got_sigint']}")
    if r["exited"]:
        a = r["after"]
        print(f"   exited: rc={r['returncode']}")
        label = "after"
    else:
        s = r["still_running"]
        print(
            "   DID NOT EXIT; still showing content="
            f"{s['shows_content']}, so quit with q"
        )
        a = r["after_q"]
        label = "after q"
    print(
        f"   {label}: original screen visible={a['shows_original']}"
        f" cursor visible={a['cursor_visible']}"
    )
    print(f"           in mode vs before: {diff_modes(before, a, 'in_mode')}")
    print(f"           out mode vs before: {diff_modes(before, a, 'out_mode')}")
    if r["prealt"] in ("vt", "buffer"):
        a = r["after_leave"]
        print(
            f"   after we leave the alt screen: original visible={a['shows_original']}"
        )
        print(f"           out mode vs before: {diff_modes(before, a, 'out_mode')}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--less", default=os.path.join(HARNESS, "less.exe"))
    parser.add_argument("--show", action="store_true", help="show the consoles")
    parser.add_argument("--child", help=argparse.SUPPRESS)
    parser.add_argument("--out", help=argparse.SUPPRESS)
    parser.add_argument("scenarios", nargs="*", choices=[[], *SCENARIOS])
    args = parser.parse_args()
    if args.child:
        run_child(args.child, args.less, args.out)
        return

    less_path = os.path.abspath(args.less)
    print(f"less: {less_path}")
    print("before = console state before less started")
    tmp = tempfile.mkdtemp(prefix="probe-less-")
    printed_before = False
    for name in args.scenarios or SCENARIOS:
        out = os.path.join(tmp, f"{name}.json")
        startupinfo: Optional[subprocess.STARTUPINFO] = None
        if not args.show:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
        cmd = [
            sys.executable,
            os.path.abspath(__file__),
            "--child", name,
            "--less", less_path,
            "--out", out,
        ]
        proc = subprocess.Popen(
            cmd,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            startupinfo=startupinfo,
        )
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            print(f"== {name}: child timed out")
            continue
        try:
            with open(out) as f:
                r = json.load(f)
            if not printed_before and "before" in r:
                printed_before = True
                print(f"  in mode: {' '.join(r['before']['in_mode'])}")
                print(f"  out mode: {' '.join(r['before']['out_mode'])}\n")
            report(name, r)
        except FileNotFoundError:
            print(f"== {name}: child wrote no result (rc={proc.returncode})")
        print()


if __name__ == "__main__":
    main()
