"""Verwaltet die lokale KI für die Oberfläche: Start beim Öffnen, Stopp beim Schließen."""

from __future__ import annotations

import threading
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from blackline2 import paths
from blackline2.ai.client import ChatClient
from blackline2.ai.server import AIServerError, LocalAIServer
from blackline2.settings import Settings

OFF = "off"
MISSING = "missing"
STARTING = "starting"
READY = "ready"
ERROR = "error"


class AIManager(QObject):
    state_changed = Signal(str, str)

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.server: LocalAIServer | None = None
        self.state = OFF
        self.message = ""
        self.model_name = ""
        self._ready = threading.Event()
        self._cancel = threading.Event()

    # ------------------------------------------------------------ Status
    def _set(self, state: str, message: str) -> None:
        self.state = state
        self.message = message
        if state == READY:
            self._ready.set()
        else:
            self._ready.clear()
        self.state_changed.emit(state, message)

    def server_path(self) -> Path | None:
        p = self.settings.llama_server_path.strip()
        if p and Path(p).is_file():
            return Path(p)
        return paths.find_llama_server() or (Path(p) if p else None)

    def model_path(self) -> Path | None:
        p = self.settings.model_path.strip()
        if p and Path(p).is_file():
            return Path(p)
        return paths.find_model() or (Path(p) if p else None)

    # ------------------------------------------------------------ Start/Stopp
    def start(self) -> None:
        """Muss im Haupt-Thread aufgerufen werden (Prozess-Kopplung unter Linux)."""
        self.stop()
        self._cancel.clear()
        mode = self.settings.ki_mode
        if mode == "aus":
            self._set(OFF, "KI ausgeschaltet (nur Eingaben und Regeln)")
            return
        if mode == "extern":
            self._set(STARTING, "Verbinde mit externer KI …")
            threading.Thread(target=self._check_external, daemon=True).start()
            return
        server, model = self.server_path(), self.model_path()
        if not server or not server.is_file() or not model or not model.is_file():
            missing = []
            if not server or not server.is_file():
                missing.append("KI-Programm (llama-server)")
            if not model or not model.is_file():
                missing.append("KI-Modell (.gguf)")
            self._set(MISSING, "Nicht eingerichtet: " + ", ".join(missing))
            return
        self.model_name = model.stem
        self.server = LocalAIServer(server, model, context=self.settings.ki_context,
                                    threads=self.settings.ki_threads,
                                    gpu_layers=self.settings.ki_gpu_layers,
                                    extra_args=self.settings.ki_extra_args)
        try:
            self.server.start()
        except AIServerError as exc:
            self._set(ERROR, str(exc))
            return
        self._set(STARTING, f"KI startet ({self.model_name}) …")
        threading.Thread(target=self._wait, args=(self.server,), daemon=True).start()

    def _wait(self, server: LocalAIServer) -> None:
        try:
            server.wait_ready(self.settings.ki_start_timeout, self._cancel)
        except AIServerError as exc:
            if server is self.server and not self._cancel.is_set():
                self._set(ERROR, str(exc))
            return
        if server is self.server:
            self._set(READY, f"KI bereit ({self.model_name})")

    def _check_external(self) -> None:
        url = self.settings.ki_extern_url.rstrip("/")
        if not url.endswith("/v1"):
            url += "/v1"
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url + "/models", timeout=5):
                pass
        except Exception as exc:  # noqa: BLE001
            self._set(ERROR, f"Externe KI nicht erreichbar: {exc}")
            return
        self.model_name = self.settings.ki_extern_model
        self._set(READY, f"Externe KI bereit ({self.model_name})")

    def stop(self) -> None:
        self._cancel.set()
        if self.server is not None:
            self.server.stop()
            self.server = None
        if self.state not in (OFF, MISSING):
            self._set(OFF, "KI beendet")

    # ------------------------------------------------------------ Nutzung
    def wait_ready(self, timeout: float, cancel: threading.Event | None = None) -> bool:
        waited = 0.0
        while waited < timeout:
            if self._ready.wait(0.5):
                return True
            if self.state in (ERROR, MISSING, OFF):
                return False
            if cancel is not None and cancel.is_set():
                return False
            waited += 0.5
        return False

    def client(self) -> ChatClient | None:
        if self.state != READY:
            return None
        if self.settings.ki_mode == "extern":
            return ChatClient(self.settings.ki_extern_url, model=self.settings.ki_extern_model,
                              timeout=self.settings.ki_request_timeout)
        if self.server is None:
            return None
        return ChatClient(self.server.base_url, api_key=self.server.api_key,
                          timeout=self.settings.ki_request_timeout)

    def log_text(self) -> str:
        if self.server is None:
            return ""
        return "\n".join(self.server.log)
