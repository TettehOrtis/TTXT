"""Single-instance lock file (Windows-safe PID liveness check)."""
import ctypes, os, sys


def _alive(pid):
    if pid <= 0:
        return False
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return code.value == 259                 # STILL_ACTIVE


def acquire(path):
    """Exit(0) silently-ish if another live instance holds the lock."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        try:
            pid = int(open(path).read().strip())
        except Exception:
            pid = 0
        if pid != os.getpid() and _alive(pid):
            print(f"another instance (pid {pid}) holds {path}; exiting", flush=True)
            sys.exit(0)
    open(path, "w").write(str(os.getpid()))
