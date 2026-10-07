"""Start und Stopp der lokalen KI (llama.cpp ``llama-server``).

Die KI läuft als Kindprozess von Blackline 2 und NUR solange das Programm
geöffnet ist:

* Beim Schließen wird der Prozess regulär beendet.
* Windows: Der Prozess hängt an einem Job-Objekt mit KILL_ON_JOB_CLOSE.
  Stürzt Blackline 2 ab oder wird es per Task-Manager beendet, schließt
  Windows das Job-Handle und beendet die KI automatisch mit.
* Linux: PR_SET_PDEATHSIG – der Kernel beendet die KI, wenn Blackline 2 endet.
* macOS/sonst: ein kleiner Wächterprozess beendet die KI, sobald Blackline 2
  nicht mehr läuft.
* Zusätzlich: Beim nächsten Start werden evtl. verwaiste KI-Prozesse beendet.

Der Server lauscht nur auf 127.0.0.1 (keine Netzwerkfreigabe) und verlangt
einen bei jedem Start neu erzeugten Zugangsschlüssel.
"""

from __future__ import annotations

import json
import os
import secrets
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

import psutil

from blackline2 import paths


class AIServerError(Exception):
    pass


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _pidfile() -> Path:
    return paths.config_dir() / "ki-server.pid"


# ---------------------------------------------------------------- Windows: Job-Objekt

class _WindowsJob:
    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._k32 = k32

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class EXTENDED(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self.handle = k32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObject fehlgeschlagen")
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = k32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info))
        if not ok:
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject fehlgeschlagen")

    def assign(self, proc: subprocess.Popen) -> None:
        ok = self._k32.AssignProcessToJobObject(self.handle, int(proc._handle))  # type: ignore[attr-defined]
        if not ok:
            raise OSError(self._ctypes.get_last_error(), "AssignProcessToJobObject fehlgeschlagen")

    def close(self) -> None:
        if self.handle:
            self._k32.CloseHandle(self.handle)
            self.handle = None


def _linux_pdeathsig() -> None:  # läuft im Kindprozess vor exec
    import ctypes

    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    libc.prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG


# Wächter ohne Python (läuft auch in gepackten Apps): startet den Server und beendet ihn,
# sobald der Elternprozess (Blackline 2) nicht mehr existiert.
_SH_GUARD = (
    'parent=$1; shift; "$@" & child=$!; '
    'trap \'kill "$child" 2>/dev/null; exit 0\' TERM INT HUP; '
    'while kill -0 "$parent" 2>/dev/null && kill -0 "$child" 2>/dev/null; do sleep 1; done; '
    'kill "$child" 2>/dev/null; wait "$child" 2>/dev/null'
)


def cleanup_stale_server() -> None:
    """Verwaisten KI-Prozess eines früheren (abgestürzten) Laufs beenden."""
    pf = _pidfile()
    try:
        data = json.loads(pf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    try:
        proc = psutil.Process(int(data.get("pid", 0)))
        if abs(proc.create_time() - float(data.get("created", 0))) < 2.0:
            for child in proc.children(recursive=True):
                child.kill()
            proc.kill()
    except (psutil.Error, ValueError, TypeError):
        pass
    try:
        pf.unlink()
    except OSError:
        pass


class LocalAIServer:
    def __init__(self, server_path: Path, model_path: Path, context: int = 8192, threads: int = 0,
                 gpu_layers: int = 99, extra_args: str = "") -> None:
        self.server_path = Path(server_path).resolve()
        self.model_path = Path(model_path).resolve()
        self.context = context
        self.threads = threads
        self.gpu_layers = gpu_layers
        self.extra_args = extra_args
        self.force_guard = False  # Tests: Wächter auch unter Linux verwenden
        self.proc: subprocess.Popen | None = None
        self.port = 0
        self.api_key = ""
        self.log: deque[str] = deque(maxlen=300)
        self._job: _WindowsJob | None = None
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def command(self) -> list[str]:
        cmd = [str(self.server_path), "-m", str(self.model_path),
               "--host", "127.0.0.1", "--port", str(self.port),
               "-c", str(self.context), "-np", "1",
               "-ngl", str(self.gpu_layers),
               "--api-key", self.api_key]
        if self.threads:
            cmd += ["-t", str(self.threads)]
        if self.extra_args.strip():
            cmd += shlex.split(self.extra_args, posix=(sys.platform != "win32"))
        return cmd

    # ------------------------------------------------------------ Lebenszyklus

    def start(self) -> None:
        """Startet den Serverprozess (kehrt sofort zurück; danach wait_ready())."""
        with self._lock:
            if self.is_running():
                return
            if not self.server_path.is_file():
                raise AIServerError(f"llama-server nicht gefunden: {self.server_path}")
            if not self.model_path.is_file():
                raise AIServerError(f"KI-Modell nicht gefunden: {self.model_path}")
            cleanup_stale_server()
            self.port = _free_port()
            self.api_key = secrets.token_urlsafe(24)
            cmd = self.command()
            kwargs: dict = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, cwd=str(self.server_path.parent))
            guard = False
            if sys.platform == "win32":
                kwargs["creationflags"] = 0x08000000 | 0x00000200  # NO_WINDOW | NEW_PROCESS_GROUP
            elif (sys.platform.startswith("linux") and threading.current_thread() is threading.main_thread()
                  and not self.force_guard):
                kwargs["preexec_fn"] = _linux_pdeathsig
            else:
                # macOS u. a.: kein Kernel-Mechanismus -> kleiner Wächter-Prozess
                cmd = ["/bin/sh", "-c", _SH_GUARD, "blackline-guard", str(os.getpid())] + cmd
                guard = True
                kwargs["start_new_session"] = True
            try:
                self.proc = subprocess.Popen(cmd, **kwargs)
            except OSError as exc:
                raise AIServerError(f"KI-Server konnte nicht gestartet werden: {exc}") from exc
            if sys.platform == "win32":
                try:
                    self._job = _WindowsJob()
                    self._job.assign(self.proc)
                except OSError as exc:
                    self.log.append(f"[Blackline] Hinweis: Job-Objekt nicht verfügbar ({exc})")
            self._reader = threading.Thread(target=self._read_output, daemon=True)
            self._reader.start()
            self._write_pidfile(guard)

    def _write_pidfile(self, guard: bool) -> None:
        if not self.proc:
            return
        try:
            created = psutil.Process(self.proc.pid).create_time()
            paths.config_dir().mkdir(parents=True, exist_ok=True)
            _pidfile().write_text(json.dumps({"pid": self.proc.pid, "created": created, "guard": guard}),
                                  encoding="utf-8")
        except (OSError, psutil.Error):
            pass

    def _read_output(self) -> None:
        proc = self.proc
        if not proc or not proc.stdout:
            return
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                self.log.append(line)

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def health(self) -> bool:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req = urllib.request.Request(self.base_url + "/health",
                                         headers={"Authorization": f"Bearer {self.api_key}"})
            with opener.open(req, timeout=2) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def wait_ready(self, timeout: float = 300, cancel: threading.Event | None = None) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                raise AIServerError("abgebrochen")
            if not self.is_running():
                tail = "\n".join(list(self.log)[-15:])
                raise AIServerError(f"Der KI-Server wurde unerwartet beendet.\n{tail}")
            if self.health():
                return
            time.sleep(0.5)
        tail = "\n".join(list(self.log)[-15:])
        raise AIServerError(f"Der KI-Server ist nicht rechtzeitig bereit geworden.\n{tail}")

    def stop(self) -> None:
        with self._lock:
            proc, self.proc = self.proc, None
            if proc is not None and proc.poll() is None:
                try:
                    if sys.platform == "win32":
                        proc.terminate()
                    else:
                        proc.send_signal(signal.SIGTERM)
                    proc.wait(timeout=8)
                except (subprocess.TimeoutExpired, OSError):
                    try:
                        proc.kill()
                        proc.wait(timeout=5)
                    except (subprocess.TimeoutExpired, OSError):
                        pass
            if self._job is not None:
                self._job.close()  # beendet sicherheitshalber alles, was im Job hängt
                self._job = None
            try:
                _pidfile().unlink()
            except OSError:
                pass
