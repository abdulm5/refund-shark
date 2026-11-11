"""Run both local services and stop both when the launcher exits."""
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    python = ROOT / ".venv/bin/python"
    if not python.exists() or not (ROOT / "web/node_modules/next").exists():
        raise SystemExit("Dependencies are missing. Run `make setup` once, then `make demo`.")
    ports = [int(os.getenv("SHARK_API_PORT", "8000")), int(os.getenv("SHARK_WEB_PORT", "3000"))]
    for port in ports:
        with socket.socket() as probe:
            # A stopped preview can leave TCP connections in TIME_WAIT. Match the servers' reuse behavior.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                raise SystemExit(f"Port {port} is unavailable. Set SHARK_API_PORT / SHARK_WEB_PORT to unused ports.")
    # The demo watches vendor status pages every two minutes unless SHARK_WATCH_INTERVAL says otherwise (0 turns it off).
    environment = {"SHARK_WATCH_INTERVAL": "120", **os.environ, "SHARK_API_PORT": str(ports[0]), "NEXT_TELEMETRY_DISABLED": "1"}
    processes = []

    def stop(*_):
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    try:
        processes.append(subprocess.Popen([str(python), "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", str(ports[0])], cwd=ROOT, env=environment, start_new_session=True))
        processes.append(subprocess.Popen(["npm", "run", "dev", "--", "--port", str(ports[1])], cwd=ROOT / "web", env=environment, start_new_session=True))
        print(f"\nRefund Shark local demo: http://127.0.0.1:{ports[1]}\nAPI docs: http://127.0.0.1:{ports[0]}/docs\nCtrl+C stops both services.\n", flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(.5)
        raise SystemExit(next((p.returncode for p in processes if p.returncode), 0))
    finally:
        stop()


if __name__ == "__main__":
    main()
