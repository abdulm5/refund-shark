"""Browser smoke check: the three demo outcomes render through the real UI.

Builds the web app against free ports and a throwaway database, starts the API and
`next start`, and reads each page with headless Chrome. Nothing touches the demo database.

Usage: .venv/bin/python scripts/ui_smoke.py [--screenshots docs/screenshots]
Set CHROME to a Chrome or Chromium binary if it is not found automatically.
It leaves web/.next built against a temporary API port; run `make build` before `npm start`.
"""
import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKS = {
    "": ["Northstar Analytics (demo)", "Harbor Logistics (demo)", "Cobalt Studio (demo)", "Ready to claim"],
    "jira-sept-claim": ["A supported request, ready for your review.", "$900.00", "Simulated customer logs", "Real public incident",
                        "Measure your own logs", "Print evidence packet"],
    "jira-sept-missing-evidence": ["A little more evidence before we ask.", "Customer request logs are missing", "$480.00", "Provisional"],
    "jira-sept-maintenance": ["This one does not qualify.", "Scheduled maintenance is excluded"],
    "view=agreements&read=atlassian": ["Read an agreement", "Saved model run", "Refund Shark can price credits under these terms", "Source library"],
    "view=incidents": ["Live status", "Disrupted JIRA availability in US East"],
    "case=jira-sept-missing-evidence&whatif=sample": ["Would still need review", "62 Downtime Minutes", "$480.00", "Nothing was saved"],
    "view=alerts": ["Simulated drill", "open a support ticket now", "Harbor Logistics (demo)", "Not alerted", "Pinecrest Labs (demo)",
                    "Customers from Ramp spend"],
}


# Screenshot file names, so README links stay stable when a check's URL changes.
SHOT_NAMES = {"": "overview", "view=agreements&read=atlassian": "agreements", "view=incidents": "incidents", "view=alerts": "alerts",
              "case=jira-sept-missing-evidence&whatif=sample": "log-upload", "view=agreements&read=atlassian&start=1": "start-case", "user-case": "user-case",
              "view=alerts&imported=1": "ramp-import"}


def post(url: str, body: dict) -> dict:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def case_checks(base: str) -> dict[str, list[str]]:
    """Start a demo case from the saved Atlassian run (checked against the full page if cached, else the committed excerpts)."""
    read = post(f"{base}/api/agreements/read", {"catalog_id": "atlassian"})
    if not read["read_id"]:
        return {"view=agreements&read=atlassian": ["A case can start from the saved Atlassian run"]}  # reported as missing
    with urllib.request.urlopen(f"{base}/api/logs/sample.csv", timeout=60) as response:
        sample = response.read().decode()
    created = post(f"{base}/api/cases", {"read_id": read["read_id"], "customer_name": "Acme Robotics (demo)", "account_reference": "DEMO-ACME-1",
                                         "period": "2026-09", "as_of": "2026-10-08T12:00:00Z", "fees": "9000", "receiving_invoice_fees": "9000",
                                         "prerequisites": {p: True for p in read["case_prerequisites"]}, "exclusions_reviewed": True,
                                         "csv": sample, "demo": True})
    return {"view=agreements&read=atlassian&start=1": ["Start a case under these terms", "Fill demo values", "Support ticket during incident"],
            f"case={created['id']}": ["A supported request, ready for your review.", "$900.00", "Acme Robotics (demo)", "Delete case", "62 Downtime Minutes"]}


def chrome() -> str:
    candidates = [os.environ.get("CHROME"), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  shutil.which("google-chrome"), shutil.which("google-chrome-stable"), shutil.which("chromium"), shutil.which("chromium-browser")]
    found = next((c for c in candidates if c and Path(c).exists()), None)
    if not found:
        raise SystemExit("Chrome or Chromium is required; set CHROME to its path.")
    return found


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait(url: str, seconds: int = 60):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except OSError:
            time.sleep(0.5)
    raise SystemExit(f"Timed out waiting for {url}")


def browse(binary: str, profile: Path, url: str, *args: str, screenshot: Path | None = None) -> str:
    """Run headless Chrome until its output is complete, then stop it: some builds never exit by themselves."""
    output = profile.with_suffix(".html")
    command = [binary, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--hide-scrollbars",
               f"--user-data-dir={profile}", "--virtual-time-budget=5000", *args,
               # Ubuntu 24.04 CI runners restrict the user namespaces Chrome's sandbox needs.
               *(["--no-sandbox"] if os.environ.get("CI") else []),
               *([f"--screenshot={screenshot}"] if screenshot else ["--dump-dom"]), url]
    with output.open("w") as sink:
        process = subprocess.Popen(command, stdout=sink, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            done = screenshot.exists() and screenshot.stat().st_size > 0 if screenshot else "</html>" in output.read_text(errors="replace")
            if done or process.poll() is not None:
                time.sleep(0.5)  # let a screenshot finish writing
                break
            time.sleep(0.25)
        return output.read_text(errors="replace")
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def check(binary: str, work: Path, web_port: int, case: str, expected: list[str], screenshots: Path | None) -> list[str]:
    query = case if "=" in case else f"case={case}" if case else ""
    url = f"http://127.0.0.1:{web_port}/" + (f"?{query}" if query else "")
    slug = (case or 'overview').replace('=', '-').replace('&', '-')
    dom = browse(binary, work / f"profile-{slug}", url)
    missing = [text for text in expected if text not in dom]
    print(f"{'ok  ' if not missing else 'FAIL'} {case or 'overview'}" + (f" — missing: {missing}" if missing else ""))
    if screenshots:
        screenshots.mkdir(parents=True, exist_ok=True)
        name = "user-case" if case.startswith("case=case-") else SHOT_NAMES.get(case, case)
        target = (screenshots / f"{name}.png").resolve()
        target.unlink(missing_ok=True)
        browse(binary, work / f"shot-{slug}", url, "--window-size=1440,1600", screenshot=target)
    return missing


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshots", type=Path, help="Also save a PNG of each checked page here")
    args = parser.parse_args()
    binary = chrome()
    api_port, web_port = free_port(), free_port()
    env = {**os.environ, "SHARK_API_PORT": str(api_port), "NEXT_TELEMETRY_DISABLED": "1"}
    work = Path(tempfile.mkdtemp(prefix="refund-shark-smoke-"))
    # Next.js resolves the API rewrite at build time, so build against this run's API port.
    subprocess.run(["npm", "run", "build"], cwd=ROOT / "web", env=env, check=True, stdout=subprocess.DEVNULL)
    processes = [
        subprocess.Popen([str(ROOT / ".venv/bin/python"), "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", str(api_port)],
                         cwd=ROOT, env={**env, "SHARK_DB": str(work / "smoke.sqlite3"), "SHARK_WATCH_INTERVAL": "120"},  # as under make demo
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
        subprocess.Popen(["npx", "next", "start", "--hostname", "127.0.0.1", "--port", str(web_port)], cwd=ROOT / "web", env=env,
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
    ]
    failures = []
    try:
        wait(f"http://127.0.0.1:{web_port}/api/health")
        # Start a simulated outage drill through the API so the alerts pages have something to show.
        drill = urllib.request.Request(f"http://127.0.0.1:{web_port}/api/watch/drill", data=b'{"action": "start"}',
                                       headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(drill, timeout=60).read()
        for case, expected in CHECKS.items():
            failures += check(binary, work, web_port, case, expected, args.screenshots)
        # After the fixed pages, so the overview screenshot shows only the bundled demo cases.
        for case, expected in case_checks(f"http://127.0.0.1:{web_port}").items():
            failures += check(binary, work, web_port, case, expected, args.screenshots)
        # Import the sample Ramp business while the drill is open: it is alerted as possibly affected.
        imported = post(f"http://127.0.0.1:{web_port}/api/spend/ramp/import", {"source": "sample"})
        if imported["new_alerts"] != 1:
            failures.append(f"sample Ramp import alerted {imported['new_alerts']} customers, expected 1")
        failures += check(binary, work, web_port, "view=alerts&imported=1", ["Lumen Freight (demo)", "Simulated sample", "plan unknown",
                                                                            "Spend shows payments to Atlassian"], args.screenshots)
    finally:
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        shutil.rmtree(work, ignore_errors=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
