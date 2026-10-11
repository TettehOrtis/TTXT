"""Keep-awake + job watchdog for the TTXT bots (runs without Claude).

  .venv\\Scripts\\python.exe tools\\watchdog.py        (started hidden by start_watchdog.bat / the Startup folder)

- Tells Windows the system is required (no idle sleep) for as long as it runs.
- Every 60 s: checks each job's supervisor .bat is running; relaunches it (hidden, detached) if missing.
- Every 10 min: logs power (charger / battery %), disk free and job status to logs/watchdog.log.
Stop: stop_watchdog.ps1 (the bots keep running; stop them with their own stop scripts).
"""
import ctypes, datetime as dt, os, subprocess, sys, time
import psutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import _lock  # noqa: E402

LOG = os.path.join(ROOT, "logs", "watchdog.log")
JOBS = [  # name, bat, args, skip-if-this-file-exists
    ("usdjpy bot", "start_usdjpy.bat", [], None),
    ("REF scanner", "start_scanner.bat", [], None),
    ("REF demo trader", "start_ref_trader.bat", ["--live"], None),
    # best-data download removed 2026-10-06 (cancelled on purpose; ~113 h left, do not resurrect it).
    # To bring it back: ("best-data download", "start_data2_best.bat", [], os.path.join(ROOT, "data_duka_full", "DONE_BEST")),
]
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
FLAGS = 0x08000000 | 0x00000008 | 0x00000200   # CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def running_bats():
    out = set()
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if (p.info["name"] or "").lower() == "cmd.exe":
                cl = " ".join(p.info["cmdline"] or []).lower()
                for _, bat, _, _ in JOBS:
                    if bat.lower() in cl:
                        out.add(bat)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return out


def launch(bat, args):
    subprocess.Popen(["cmd", "/c", os.path.join(ROOT, bat), *args], cwd=ROOT, creationflags=FLAGS,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)


def power():
    b = psutil.sensors_battery()
    disk = psutil.disk_usage(ROOT).free / 1e9
    if b is None:
        return f"no battery info, disk free {disk:.0f} GB"
    return f"{'CHARGER' if b.power_plugged else 'BATTERY'} {b.percent:.0f}%, disk free {disk:.0f} GB"


def main():
    _lock.acquire(os.path.join(ROOT, "logs", "watchdog.lock"))
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    log(f"watchdog start (pid {os.getpid()}): keep-awake ON; {power()}")
    last_status = 0.0
    warned_battery = False
    while True:
        try:
            up = running_bats()
            for name, bat, args, done in JOBS:
                if done and os.path.exists(done):
                    continue
                if bat not in up:
                    log(f"{name} NOT running -> relaunching {bat} {' '.join(args)}")
                    launch(bat, args)
            b = psutil.sensors_battery()
            if b is not None and not b.power_plugged and not warned_battery:
                log(f"WARNING: on battery ({b.percent:.0f}%). Plug in the charger or the laptop will die.")
                warned_battery = True
            if b is not None and b.power_plugged:
                warned_battery = False
            if time.time() - last_status > 600:
                ok = [n for n, bat, _, _ in JOBS if bat in up]
                log(f"status: {power()}; running: {', '.join(ok) or 'none'}")
                last_status = time.time()
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        except Exception as e:  # never die
            log(f"error {e!r}")
        time.sleep(60)


if __name__ == "__main__":
    main()
