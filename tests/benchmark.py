#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "tests/integration")]
from coordinator_test import Harness


def cpu_ticks(pid: int) -> int:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except FileNotFoundError:
        return 0
    return sum(int(fields[index]) for index in (11, 12, 13, 14))


def benchmark(windows: int, seconds: float, directory: Path) -> dict:
    server = Harness(directory)
    client = None
    try:
        for index in range(1, windows):
            server.t("new-window", "-d", "-t", "work", "-n", f"window-{index}", "sleep 86400")
        client = subprocess.Popen(["tmux", "-S", server.socket, "-C", "attach-session", "-f", "no-output,ignore-size", "-t", "work"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=server.env)
        server.wait(lambda: server.t("display-message", "-p", "#{session_attached}"), "1")
        server.open()
        server.wait(lambda: server.api("doctor")["connected_views"], windows, seconds=15)
        time.sleep(.5)
        before = server.api("doctor")
        pids = [server.identity.server_pid, before["pid"]]
        pids.extend(int(line.split("|")[1]) for line in server.t("list-panes", "-a", "-F", "#{@tmux_sidebar_owned}|#{pane_pid}").splitlines() if line.startswith("1|"))
        initial = sum(cpu_ticks(pid) for pid in pids)
        started = time.monotonic()
        time.sleep(seconds)
        elapsed = time.monotonic() - started
        used = sum(cpu_ticks(pid) for pid in pids) - initial
        after = server.api("doctor")
        return {"windows": windows, "connected_views": after["connected_views"], "seconds": round(elapsed, 3), "idle_tmux_queries": after["tmux_calls"] - before["tmux_calls"], "cpu_percent_one_core": round(100 * used / os.sysconf("SC_CLK_TCK") / elapsed, 3)}
    finally:
        if client is not None:
            client.terminate()
            client.communicate(timeout=3)
        server.close()


def main():
    parser = argparse.ArgumentParser(description="Linux idle benchmark with an isolated tmux server and one attached control client")
    parser.add_argument("--windows", nargs="+", type=int, default=[1, 10, 50])
    parser.add_argument("--seconds", type=float, default=5)
    args = parser.parse_args()
    if not Path("/proc/self/stat").exists():
        parser.error("CPU sampling requires Linux /proc")
    with tempfile.TemporaryDirectory(prefix="binder-bench-") as directory:
        for index, windows in enumerate(args.windows):
            print(json.dumps(benchmark(windows, args.seconds, Path(directory) / str(index))), flush=True)


if __name__ == "__main__":
    main()
