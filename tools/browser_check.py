"""Run the overlay in a real browser and report what the page does.

The harness is two files, and the split is where the judgement lives:

* ``tools/browser_check.mjs`` drives a tab over the DevTools protocol and prints one
  object of measurements — ``tests/test_browser_harness.py`` says what those numbers have
  to be. Assertions belong with the rest of the suite's, next to the code they are about.
* this script owns the world those measurements need: a server on a port nothing else is
  using, a browser with a profile of its own, and the two shut down again whatever
  happens.

It exists because nothing in this project can otherwise see the page. The sea, the sphere
and the panel glow are canvas and computed style: a test that reads the files can say
whether a token was written, never whether anything was lit. Those measurements were made
by hand while the ports were written, and this is where they live now.

Nothing here needs a third-party package: the server is this repository's own module, the
browser is one this machine already has, and the driver talks to it over the same
WebSocket the browser's own DevTools does.

    python tools/browser_check.py                # start a server, drive a browser
    python tools/browser_check.py --base http://127.0.0.1:8000   # use one already up
    python tools/browser_check.py --headed --json report.json    # watch it, keep the report

Exits 0 when the driver finished, nonzero when it could not run or the page threw. The
report's numbers are not judged here; see the test.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRIVER = Path(__file__).resolve().with_name("browser_check.mjs")

#: Where Chrome and its descendants live, in the order worth trying. A machine can have
#: several of these; the first that exists is the one this project has been checked in.
BROWSERS = {
    "win32": [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
    ],
    "darwin": [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    ],
    "linux": [
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/chromium"),
        Path("/usr/bin/chromium-browser"),
        Path("/usr/bin/microsoft-edge"),
    ],
}


def browser() -> Path | None:
    """The browser to drive: the environment's own pick first, then the usual places."""
    chosen = os.environ.get("PITCHFRAME_CHROME")
    if chosen and Path(chosen).exists():
        return Path(chosen)
    for candidate in BROWSERS.get(sys.platform, []):
        if str(candidate) and candidate.exists():
            return candidate
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def free_port() -> int:
    """A port the machine will hand out: asked for by binding to zero and finding out.

    Asked for rather than guessed at, because a fixed port is a test that fails on the
    machine where something else already has it — which is the sort of failure that gets
    a suite disabled.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_health(base: str, timeout: float = 60.0, process: subprocess.Popen | None = None) -> dict:
    """Wait until the overlay answers its own readiness probe, and say what it said."""
    deadline = time.monotonic() + timeout
    last = "no answer yet"
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"the server exited with code {process.returncode} before it answered")
        try:
            with urllib.request.urlopen(f"{base}/api/health", timeout=2.0) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            last = str(error)
        time.sleep(0.25)
    raise RuntimeError(f"nothing answered {base}/api/health within {timeout:.0f}s ({last})")


def wait_for_devtools(port: int, timeout: float = 60.0) -> None:
    """Wait until the browser is listening on its debug port."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2.0) as response:
                json.loads(response.read().decode("utf-8"))
                return
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            pass
        time.sleep(0.25)
    raise RuntimeError(f"the browser never opened its DevTools port on 127.0.0.1:{port}")


def stop(process: subprocess.Popen | None) -> None:
    """Stop a process and everything it started, without touching anything else."""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def run(args: argparse.Namespace) -> int:
    chrome = Path(args.chrome) if args.chrome else browser()
    if chrome is None:
        print("error: no Chrome or Edge found. Pass --chrome <path>.", file=sys.stderr)
        return 2
    node = shutil.which(args.node)
    if node is None:
        print(f"error: {args.node!r} is not on PATH; the driver is a Node script.", file=sys.stderr)
        return 2

    server = None
    browser_process = None
    profile = None
    base = args.base
    # The two ports are asked for separately: a server and a debugger that happened to be
    # given the same number would be a race between two things binding it.
    cdp_port = args.cdp_port or free_port()

    try:
        if base is None:
            port = args.port or free_port()
            base = f"http://127.0.0.1:{port}"
            print(f"starting the overlay on {base}", file=sys.stderr)
            server = subprocess.Popen(
                [sys.executable, "-m", "pitchframe", "--overlay", "--port", str(port)],
                cwd=str(ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            print(f"using the overlay already at {base}", file=sys.stderr)

        health = wait_for_health(base, process=server)
        code = health.get("code", {})
        if code.get("stale"):
            # The page would answer 503 for every document, which reads as a broken page
            # rather than as a stale process — the one failure this project already has a
            # message for, and the harness is not going to disguise it.
            print(f"error: the process at {base} is older than the files here; restart it.", file=sys.stderr)
            return 2

        profile = Path(tempfile.mkdtemp(prefix="pitchframe-browser-"))
        profile_path = str(profile).replace("\\", "/")
        launch = [
            str(chrome),
            f"--remote-debugging-port={cdp_port}",
            f"--user-data-dir={profile_path}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-sync",
            "--disable-background-networking",
            "--window-size=1440,900",
            # A window on somebody's desktop, opened to take four screenshots, is a window
            # they have to close. Headless unless asked otherwise.
            *( [] if args.headed else ["--headless=new", "--hide-scrollbars"] ),
            base + "/",
        ]
        print(f"driving {chrome.name} (pid pending) against {base}", file=sys.stderr)
        browser_process = subprocess.Popen(
            launch,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        wait_for_devtools(cdp_port)

        completed = subprocess.run(
            [node, str(DRIVER)],
            cwd=str(ROOT),
            env={
                **os.environ,
                "PF_BASE": base,
                "CDP_PORT": str(cdp_port),
                "PF_MATCH": args.match,
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=args.timeout,
        )
        if completed.stderr.strip():
            print(completed.stderr.strip(), file=sys.stderr)
        if completed.returncode != 0:
            print(f"error: the driver exited with code {completed.returncode}", file=sys.stderr)
            print(completed.stdout.strip()[-4000:], file=sys.stderr)
            return completed.returncode

        try:
            report = json.loads(completed.stdout)
        except ValueError as error:
            print(f"error: the driver did not print a report ({error})", file=sys.stderr)
            print(completed.stdout.strip()[-4000:], file=sys.stderr)
            return 2

        text = json.dumps(report, indent=2)
        if args.json:
            Path(args.json).write_text(text + "\n", encoding="utf-8")
            print(f"report written to {args.json}", file=sys.stderr)
        if not args.quiet:
            print(text)

        problems = report.get("problems") or []
        if problems:
            print(f"error: the page reported {len(problems)} problem(s):", file=sys.stderr)
            for problem in problems:
                print(f"  {problem['at']} {problem['kind']}: {problem['text']}", file=sys.stderr)
            return 1
        return 0
    except subprocess.TimeoutExpired:
        print(f"error: the driver did not finish within {args.timeout:.0f}s", file=sys.stderr)
        return 2
    except (RuntimeError, urllib.error.URLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    finally:
        stop(browser_process)
        stop(server)
        if profile is not None:
            shutil.rmtree(profile, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", help="an overlay already running, instead of starting one")
    parser.add_argument("--port", type=int, help="port for the server this script starts")
    parser.add_argument("--cdp-port", type=int, help="port for the browser's DevTools endpoint")
    parser.add_argument("--chrome", help="path to the browser to drive")
    parser.add_argument("--node", default="node", help="the driver's interpreter (default: node)")
    parser.add_argument("--match", default="/match/matchday", help="the match path to open")
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument("--json", help="write the report here as well as printing it")
    parser.add_argument("--quiet", action="store_true", help="do not print the report")
    parser.add_argument("--timeout", type=float, default=300.0, help="seconds for the driver")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
