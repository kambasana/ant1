"""Control server for the C2 dashboard: run it once, drive games from the page.

Serves the dashboard two things it cannot get anywhere else:

  GET  /state    the running game's latest observation, plus whether a game
                 is running at all, what it is, and how long it has been going
  POST /control  {"action": "start"|"stop", ...} to launch or kill a game

This is deliberately a separate process from the game. The dashboard needs to
work when no game exists -- that is precisely when you want to start one --
and a control server living inside a game would die with it, which is what
made the dashboard look broken every time a run ended.

    python -m milsim.control
    python -m milsim.control --port 8321
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from milsim.dashboard_feed import clear, snapshot, state_path, STATE_ENV

DEFAULT_PORT = 8321
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_started_at: float = 0.0
_config: dict = {}
_last_error: str = ""


def _status() -> str:
    """running | ended | stopped | idle -- what the badge shows."""
    with _lock:
        if _proc is None:
            return "idle"
        code = _proc.poll()
        if code is None:
            return "running"
        return "ended" if code == 0 else "stopped"


def start_game(mode="rule", model="", provider="", turns=2000, server="") -> dict:
    """Launch a game as a subprocess. Refuses if one is already running."""
    global _proc, _started_at, _config, _last_error

    if _status() == "running":
        return {"ok": False, "error": "a game is already running"}

    clear()  # so the dashboard cannot show the previous game as this one
    cmd = [sys.executable, "-m", "milsim", "--mode", mode,
           "--dashboard", "--max-turns", str(int(turns))]
    if mode in ("text", "fc"):
        # text and fc refuse to start without an explicit --model even when
        # the provider config supplies one, so pass it through.
        cmd += ["--model", model or os.environ.get("MILSIM_LLM_MODEL", "gemma3:4b")]
        if provider:
            cmd += ["--provider", provider]
    if server:
        cmd += ["--server", server]

    env = dict(os.environ)
    env[STATE_ENV] = state_path()

    try:
        proc = subprocess.Popen(
            cmd, cwd=REPO_ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        _last_error = str(exc)
        return {"ok": False, "error": _last_error}

    with _lock:
        _proc = proc
        _started_at = time.time()
        _config = {"mode": mode, "model": model, "turns": turns}
        _last_error = ""
    return {"ok": True, "pid": proc.pid, "config": _config}


def stop_game() -> dict:
    global _proc
    with _lock:
        proc = _proc
    if proc is None or proc.poll() is not None:
        return {"ok": True, "note": "no game running"}
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    return {"ok": True}


def payload() -> dict:
    status = _status()
    snap = snapshot()
    with _lock:
        cfg = dict(_config)
        elapsed = int(time.time() - _started_at) if _started_at else 0
        err = _last_error
    out = {
        "status": status,
        "connected": status == "running" and snap is not None,
        "has_state": snap is not None,
        "config": cfg,
        "elapsed_s": elapsed if status == "running" else 0,
        "error": err,
    }
    if snap:
        out.update(snap)
    else:
        out.setdefault("turn", 0)
    return out


class _Handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        # The dashboard is opened from disk, so its origin is "null" and every
        # request is cross-origin. Without these the browser drops the reply
        # and the page silently keeps its placeholder data.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):  # noqa: N802
        self._send({})

    def do_GET(self):  # noqa: N802
        if self.path.split("?")[0] in ("/", "/state", "/state.json"):
            self._send(payload())
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        if self.path.split("?")[0] != "/control":
            self._send({"error": "not found"}, 404)
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, OSError):
            self._send({"ok": False, "error": "bad request"}, 400)
            return

        action = req.get("action")
        if action == "start":
            self._send(start_game(
                mode=req.get("mode", "rule"),
                model=req.get("model", ""),
                provider=req.get("provider", ""),
                turns=req.get("turns", 2000),
                server=req.get("server", ""),
            ))
        elif action == "stop":
            self._send(stop_game())
        else:
            self._send({"ok": False, "error": f"unknown action {action!r}"}, 400)

    def log_message(self, *args):
        pass  # a line per poll would bury everything else


def main():
    ap = argparse.ArgumentParser(description="C2 dashboard control server")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()

    srv = HTTPServer(("127.0.0.1", args.port), _Handler)
    print(f"C2 control server: http://127.0.0.1:{args.port}/state")
    print(f"  state file: {state_path()}")
    print("  open milsim/c2-dashboard.html and use the game controls")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        stop_game()
        print("\nstopped")


if __name__ == "__main__":
    main()
