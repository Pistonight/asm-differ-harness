"""
Non-interactive checks of less.exe: version, that it accepts every flag
diff.py passes it, and that the tail | less pipeline passes text through.

With stdout not a terminal, less behaves like cat, so a bad flag shows up as
an error message / exit code instead of an interactive screen.

Usage: python probes/probe_less_flags.py [path\\to\\less.exe]
"""

import os
import shutil
import subprocess
import sys

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LESS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HARNESS, "less.exe")

# mirrors diff.py on the kill_less_fix branch
BUFFER_CMD = ["tail", "-c", str(10**9)]
FLAG_SETS = {
    "run_sync (alt screen)": ["-SRic", "-+F", "-+X", "-#6"],
    "watch mode (no alt screen)": ["-SRic", "-+F", "-X", "-#6"],
    # candidates for a Windows implementation
    "+ -K (quit on interrupt)": ["-SRic", "-+F", "-X", "-#6", "-K"],
    # sanity check that a rejected flag is detected (rc is 0 even then)
    "sanity: bogus flag": ["--this-is-not-an-option"],
}
TEXT = "".join(f"line {i} \x1b[31mRED\x1b[0m end\n" for i in range(50)).encode()


def env() -> dict:
    e = dict(os.environ)
    for var in ["LESS", "LESSOPEN", "LESSCLOSE"]:
        e.pop(var, None)
    return e


def main() -> None:
    print(f"less: {LESS}")
    out = subprocess.run([LESS, "--version"], capture_output=True, env=env())
    print("version:", out.stdout.decode(errors="replace").splitlines()[0])

    # what diff.py would launch once run.py has put the harness dir on PATH
    path = HARNESS + os.pathsep + os.environ.get("PATH", "")
    print("which less (run.py PATH):", shutil.which("less", path=path))
    print("which tail:", shutil.which("tail"))
    print()

    for name, flags in FLAG_SETS.items():
        r = subprocess.run(
            [LESS, *flags], input=TEXT, capture_output=True, env=env(), timeout=10
        )
        ok = r.returncode == 0 and r.stdout == TEXT and not r.stderr
        if name.startswith("sanity"):
            ok = not ok
        print(f"[{'OK ' if ok else 'BAD'}] {name}: {' '.join(flags)}")
        print(f"      rc={r.returncode} stdout_matches={r.stdout == TEXT}")
        if r.stderr or (r.stdout != TEXT and r.stdout):
            msg = (r.stderr or r.stdout).decode(errors="replace").strip()
            print(f"      output: {msg[:200]!r}")

    # the exact pipeline shape diff.py uses
    buf = subprocess.Popen(BUFFER_CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    less = subprocess.Popen(
        [LESS, *FLAG_SETS["watch mode (no alt screen)"]],
        stdin=buf.stdout,
        stdout=subprocess.PIPE,
        env=env(),
    )
    assert buf.stdin and buf.stdout
    buf.stdout.close()
    buf.stdin.write(TEXT)
    buf.stdin.close()
    piped, _ = less.communicate(timeout=10)
    buf.wait(timeout=10)
    ok = piped == TEXT and less.returncode == 0
    print(f"[{'OK ' if ok else 'BAD'}] tail | less pipeline: rc={less.returncode}")


if __name__ == "__main__":
    main()
