"""Einrichtung: lädt die lokale KI (llama.cpp + Modell) und die OCR-Sprachdaten herunter.

Aufruf im Programmordner:
    python -m blackline2.setup_ki                  # alles (Modell "ausgewogen", nur CPU)
    python -m blackline2.setup_ki --modell schnell # kleineres, schnelleres Modell
    python -m blackline2.setup_ki --gpu vulkan     # Grafikkarte nutzen (AMD/Intel/NVIDIA)
    python -m blackline2.setup_ki --nur-ocr        # nur deutsche Texterkennung
    python -m blackline2.setup_ki --test           # prüfen, ob die KI antwortet
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

from blackline2 import paths

MODELS = {
    "schnell": {
        "repo": "unsloth/Qwen3-4B-Instruct-2507-GGUF",
        "pattern": r"Q4_K_M\.gguf$",
        "info": "Qwen3 4B (ca. 2,5 GB) – für ältere Rechner ohne Grafikkarte",
    },
    "ausgewogen": {
        "repo": "Qwen/Qwen3-8B-GGUF",
        "pattern": r"Q4_K_M\.gguf$",
        "info": "Qwen3 8B (ca. 5 GB) – guter Kompromiss, empfohlen ab 16 GB RAM",
    },
    "gruendlich": {
        "repo": "unsloth/gemma-3-12b-it-GGUF",
        "pattern": r"Q4_K_M\.gguf$",
        "info": "Gemma 3 12B (ca. 7 GB) – sehr gutes Deutsch, empfohlen mit Grafikkarte oder 32 GB RAM",
    },
}

TESSDATA = {
    "standard": "https://github.com/tesseract-ocr/tessdata/raw/main/{lang}.traineddata",
    "best": "https://github.com/tesseract-ocr/tessdata_best/raw/main/{lang}.traineddata",
}

UA = {"User-Agent": "Blackline2-Setup"}

# Fortschritt: in der Konsole gedruckt oder (aus der Oberfläche) an einen Callback gemeldet
REPORTER: Callable[[str, int, int], None] | None = None
CANCEL: threading.Event | None = None


class SetupCancelled(Exception):
    pass


def _say(msg: str, done: int = 0, total: int = 0) -> None:
    if REPORTER is not None:
        REPORTER(msg, done, total)
    else:
        print(msg, flush=True)


def _check_cancel() -> None:
    if CANCEL is not None and CANCEL.is_set():
        raise SetupCancelled()


# ---------------------------------------------------------------- Download-Hilfen

def _contexts():
    yield ssl.create_default_context()
    try:
        import certifi

        yield ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass


def _open(url: str, headers: dict | None = None, timeout: int = 60):
    last: Exception | None = None
    for ctx in _contexts():
        try:
            req = urllib.request.Request(url, headers={**UA, **(headers or {})})
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ssl.SSLError):
                last = exc
                continue
            raise
    raise last or RuntimeError("Download fehlgeschlagen")


def get_json(url: str, headers: dict | None = None) -> object:
    with _open(url, headers) as r:
        return json.loads(r.read().decode("utf-8"))


def download(url: str, target: Path, label: str) -> Path:
    """Download mit Fortschrittsanzeige und Fortsetzen nach Abbruch."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    for attempt in range(5):
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with _open(url, headers, timeout=120) as r:
                if have and r.status != 206:
                    have = 0  # Server kann nicht fortsetzen -> neu
                total = r.headers.get("Content-Length")
                total = int(total) + have if total else None
                mode = "ab" if have else "wb"
                done = have
                last = 0.0
                with open(part, mode) as f:
                    while True:
                        _check_cancel()
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        now = time.monotonic()
                        if now - last > 0.5:
                            last = now
                            if total:
                                text = (f"{label}: {done / 2**20:,.0f} / {total / 2**20:,.0f} MB "
                                        f"({100 * done / total:4.1f} %)")
                            else:
                                text = f"{label}: {done / 2**20:,.0f} MB"
                            if REPORTER is not None:
                                REPORTER(text, done, total or 0)
                            else:
                                print("\r  " + text, end="", flush=True)
            if REPORTER is None:
                print()
            part.replace(target)
            return target
        except urllib.error.HTTPError as exc:
            if exc.code == 416:  # schon vollständig
                part.replace(target)
                return target
            raise
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            _say(f"Verbindungsproblem ({exc}) – neuer Versuch {attempt + 2}/5 …")
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Download von {label} nicht möglich.")


# ---------------------------------------------------------------- OCR

def setup_ocr(variant: str = "standard", langs: tuple[str, ...] = ("deu", "eng")) -> None:
    _say("== Texterkennung (OCR-Sprachdaten) ==")
    target_dir = paths.tessdata_dir()
    for lang in langs:
        target = target_dir / f"{lang}.traineddata"
        if target.exists() and target.stat().st_size > 1_000_000:
            _say(f"  {lang}.traineddata ist bereits vorhanden.")
            continue
        download(TESSDATA[variant].format(lang=lang), target, f"{lang}.traineddata")
    _say(f"  OK – gespeichert in {target_dir}")


# ---------------------------------------------------------------- llama.cpp

_GPU_WORDS = ("cuda", "vulkan", "hip", "rocm", "sycl", "opencl", "musa", "kompute", "openvino", "cann",
              "radeon", "metal-off")
_ARCHIVE = re.compile(r"\.(zip|tar\.gz|tgz)$", re.I)


def _system_words() -> tuple[tuple[str, ...], tuple[str, ...]]:
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if sys.platform == "win32":
        osw: tuple[str, ...] = ("win",)
    elif sys.platform == "darwin":
        osw = ("macos", "osx", "darwin", "apple")
    else:
        osw = ("ubuntu", "linux")
    arch = ("arm64", "aarch64") if arm else ("x64", "x86_64", "amd64")
    return osw, arch


def _asset_score(name: str, gpu: str) -> int:
    """Wie gut passt ein Paketname zu diesem System? 0 = gar nicht."""
    n = name.lower()
    if not _ARCHIVE.search(n) or "bin" not in n or n.startswith("cudart"):
        return 0
    osw, arch = _system_words()
    tokens = set(re.split(r"[-_.]", n))
    if not any(w in tokens for w in osw):
        return 0
    if not any(a in tokens for a in arch):
        # macOS-Pakete nennen die Architektur teils nicht
        if sys.platform != "darwin":
            return 0
    has_gpu = [w for w in _GPU_WORDS if w in n]
    score = 10
    if sys.platform == "darwin":
        return score + (5 if not has_gpu else 0)
    if gpu == "cpu":
        if has_gpu:
            return 0
        score += 5 if "cpu" in tokens else 0
    else:
        if gpu not in has_gpu:
            return 0
        if gpu == "cuda" and re.search(r"cuda-?12", n):
            score += 3
    return score


def pick_asset(assets: list[dict], gpu: str) -> dict | None:
    scored = [(_asset_score(a.get("name", ""), gpu), a) for a in assets]
    scored = [x for x in scored if x[0] > 0]
    if not scored:
        return None
    return max(scored, key=lambda x: x[0])[1]


_API = "https://api.github.com/repos/ggml-org/llama.cpp"
_WEB = "https://github.com/ggml-org/llama.cpp"


def _api_headers() -> dict:
    h = {"Accept": "application/vnd.github+json"}
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _releases_from_web(limit: int = 8) -> list[dict]:
    """Ausweichweg ohne GitHub-API (z. B. bei Ratenbegrenzung): Release-Seiten auslesen."""
    with _open(f"{_WEB}/releases") as r:
        html = r.read().decode("utf-8", errors="replace")
    tags: list[str] = []
    for t in re.findall(r"/ggml-org/llama\.cpp/releases/tag/([^\"?#/<>\s]+)", html):
        if t not in tags:
            tags.append(t)
    releases = []
    for tag in tags[:limit]:
        with _open(f"{_WEB}/releases/expanded_assets/{tag}") as r:
            page = r.read().decode("utf-8", errors="replace")
        names: list[str] = []
        for n in re.findall(rf"/ggml-org/llama\.cpp/releases/download/{re.escape(tag)}/([^\"?#<>\s]+)", page):
            if n not in names:
                names.append(n)
        releases.append({"tag_name": tag, "assets": [
            {"name": urllib.parse.unquote(n), "browser_download_url": f"{_WEB}/releases/download/{tag}/{n}"}
            for n in names]})
    return releases


def list_llama_releases() -> list[dict]:
    try:
        rels = get_json(f"{_API}/releases?per_page=15", _api_headers())
        if isinstance(rels, list) and rels:
            return rels
    except urllib.error.HTTPError as exc:
        _say(f"  GitHub-API nicht nutzbar ({exc.code}) – lese Release-Seite …")
    return _releases_from_web()


def find_llama_asset(gpu: str) -> tuple[dict, dict]:
    releases = list_llama_releases()
    for rel in releases:
        if rel.get("draft"):
            continue
        asset = pick_asset(rel.get("assets", []), gpu)
        if asset:
            return rel, asset
    sample = next((r for r in releases if len(r.get("assets", [])) > 3), releases[0] if releases else {})
    names = "\n    ".join(a.get("name", "") for a in sample.get("assets", [])[:60])
    raise RuntimeError(f"Kein passendes llama.cpp-Paket für dieses System gefunden "
                       f"(Release {sample.get('tag_name')}). Verfügbar:\n    {names}")


def _extract(archive: Path, dest: Path) -> None:
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for member in z.namelist():
                p = (dest / member).resolve()
                if not str(p).startswith(str(dest.resolve())):
                    raise RuntimeError("Unsicherer Pfad im Archiv")
            z.extractall(dest)
    else:
        with tarfile.open(archive) as t:
            try:
                t.extractall(dest, filter="data")
            except TypeError:  # ältere Python-Version ohne filter-Parameter
                t.extractall(dest)


def setup_llama(gpu: str = "cpu", update: bool = True) -> Path:
    _say("== KI-Programm (llama.cpp / llama-server) ==")
    existing = paths.find_llama_server()
    if existing is not None and not update:
        _say(f"  bereits vorhanden – wird weiterverwendet: {existing}")
        return existing
    rel, asset = find_llama_asset(gpu)
    assets = rel.get("assets", [])
    _say(f"  Version {rel.get('tag_name')}: {asset['name']}")
    dest = paths.ki_server_dir()
    tmp = paths.ki_dir() / "_download"
    archive = download(asset["browser_download_url"], tmp / asset["name"], asset["name"])
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    _extract(archive, dest)
    if gpu == "cuda" and sys.platform == "win32":
        m = re.search(r"cuda-([\d.]+)-", asset["name"])
        for a in assets:
            if a["name"].startswith("cudart") and m and m.group(1) in a["name"]:
                rt = download(a["browser_download_url"], tmp / a["name"], a["name"])
                server = paths.find_llama_server()
                _extract(rt, server.parent if server else dest)
    shutil.rmtree(tmp, ignore_errors=True)
    server = paths.find_llama_server()
    if server is None:
        raise RuntimeError("llama-server wurde im Paket nicht gefunden.")
    if sys.platform != "win32":
        for f in server.parent.iterdir():
            if f.is_file() and (f.name.startswith("llama-") or f.suffix in (".so", ".dylib") or "." not in f.name):
                f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    if sys.platform == "darwin":
        # Apple Silicon führt nur signierte Programme aus – eine Ad-hoc-Signatur genügt
        for f in server.parent.iterdir():
            if f.is_file():
                subprocess.run(["codesign", "--force", "--sign", "-", str(f)], capture_output=True, check=False)
    _say(f"  OK – {server}")
    return server


# ---------------------------------------------------------------- Modell

def setup_model(choice: str) -> Path:
    info = MODELS[choice]
    _say(f"== KI-Modell: {info['info']} ==")
    existing = paths.find_known_model(choice)
    if existing is not None:
        # schon geladen (auch von einer früheren Version oder einem anderen KI-Programm): nicht doppelt laden
        _say(f"  bereits vorhanden – wird weiterverwendet: {existing}")
        target = existing
    else:
        target = _download_model(info)
    # andere Modelle nicht löschen, aber dieses als Standard eintragen
    from blackline2.settings import Settings

    s = Settings.load()
    s.model_path = str(target)
    s.save()
    _say(f"  OK – {target}")
    return target


def _download_model(info: dict) -> Path:
    data = get_json(f"https://huggingface.co/api/models/{info['repo']}")
    files = [s["rfilename"] for s in data.get("siblings", [])] if isinstance(data, dict) else []
    cands = [f for f in files if re.search(info["pattern"], f, re.I) and "mmproj" not in f.lower()]
    if not cands:
        raise RuntimeError(f"Keine passende Modelldatei in {info['repo']} gefunden.")
    fname = sorted(cands, key=len)[0]
    target = paths.ki_models_dir() / Path(fname).name
    if target.exists():
        _say(f"  {target.name} ist bereits vorhanden.")
    else:
        url = f"https://huggingface.co/{info['repo']}/resolve/main/{fname}?download=true"
        download(url, target, target.name)
    return target


# ---------------------------------------------------------------- Gesamtablauf (für die Oberfläche)

def run_setup(modell: str = "ausgewogen", gpu: str = "cpu", ocr: bool = True, ki: bool = True,
              reporter: Callable[[str, int, int], None] | None = None,
              cancel: threading.Event | None = None, update_llama: bool = True) -> Path | None:
    """Alles einrichten; Fortschritt an reporter(msg, done, total). Liefert den Modellpfad."""
    global REPORTER, CANCEL
    REPORTER, CANCEL = reporter, cancel
    try:
        if ocr:
            setup_ocr()
        model = None
        if ki:
            setup_llama(gpu, update=update_llama)
            model = setup_model(modell)
        _say("Fertig.")
        return model
    finally:
        REPORTER, CANCEL = None, None


# ---------------------------------------------------------------- Selbsttest

def self_test() -> bool:
    from blackline2.ai.client import ChatClient
    from blackline2.ai.detector import AIDetector
    from blackline2.ai.server import LocalAIServer
    from blackline2.detect_inputs import UserInputs
    from blackline2.labels import PersonRegistry
    from blackline2.model import PageData, Word
    from blackline2.settings import Settings

    _say("== Selbsttest der KI ==")
    s = Settings.load()
    server = Path(s.llama_server_path) if s.llama_server_path else paths.find_llama_server()
    model = Path(s.model_path) if s.model_path else paths.find_model()
    if not server or not model:
        _say("  KI ist noch nicht eingerichtet.")
        return False
    srv = LocalAIServer(server, model, s.ki_context, s.ki_threads, s.ki_gpu_layers, s.ki_extra_args)
    t0 = time.monotonic()
    try:
        srv.start()
        _say("  Modell wird geladen …")
        srv.wait_ready(s.ki_start_timeout)
        _say(f"  geladen nach {time.monotonic() - t0:.0f} s")
        text = ("Sehr geehrte Frau Petra Musterfrau, wie mit Herrn Kinzel besprochen, "
                "überweisen Sie bitte an Herrn Jens Beispiel, Bahnhofstraße 7, 35578 Wetzlar.")
        page = PageData(0, 595, 842, [Word(t, (0, 0, 1, 1), 0) for t in text.split()])
        det = AIDetector(ChatClient(srv.base_url, srv.api_key, timeout=600), PersonRegistry(),
                         UserInputs(mandant_name="Robin Kinzel"))
        t1 = time.monotonic()
        found = det.analyze_page(page, 1, 1)
        _say(f"  Antwort nach {time.monotonic() - t1:.0f} s:")
        for f in found:
            _say(f"    - {f.kategorie:12} {f.text!r:32} ({f.bezug})")
        return bool(found)
    except Exception as exc:  # noqa: BLE001
        _say(f"  FEHLER: {exc}")
        _say("  Letzte Meldungen des KI-Servers:\n    " + "\n    ".join(list(srv.log)[-10:]))
        return False
    finally:
        srv.stop()
        _say("  KI wieder beendet.")


def check_sources() -> bool:
    ok = True
    for gpu in ("cpu", "vulkan", "cuda"):
        try:
            rel, a = find_llama_asset(gpu)
            _say(f"  llama.cpp {gpu:6}: {rel.get('tag_name')} {a['name']}")
        except RuntimeError as exc:
            _say(f"  llama.cpp {gpu:6}: NICHT GEFUNDEN – {exc}")
            ok &= gpu != "cpu"
    for key, info in MODELS.items():
        data = get_json(f"https://huggingface.co/api/models/{info['repo']}")
        files = [s["rfilename"] for s in data.get("siblings", [])] if isinstance(data, dict) else []
        cands = [f for f in files if re.search(info["pattern"], f, re.I) and "mmproj" not in f.lower()]
        _say(f"  Modell {key:10}: {cands[0] if cands else 'NICHT GEFUNDEN'}")
        ok &= bool(cands)
    for variant, url in TESSDATA.items():
        try:
            with _open(url.format(lang="deu"), {"Range": "bytes=0-99"}):
                _say(f"  OCR {variant:8}: erreichbar")
        except Exception as exc:  # noqa: BLE001
            _say(f"  OCR {variant:8}: FEHLER {exc}")
            ok = False
    return ok


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m blackline2.setup_ki",
                                 description="Lädt die lokale KI und die OCR-Sprachdaten für Blackline 2.")
    ap.add_argument("--modell", choices=list(MODELS), default="ausgewogen",
                    help="KI-Modell: " + "; ".join(f"{k} = {v['info']}" for k, v in MODELS.items()))
    ap.add_argument("--gpu", choices=["cpu", "vulkan", "cuda"], default="cpu",
                    help="cpu (überall), vulkan (die meisten Grafikkarten), cuda (NVIDIA)")
    ap.add_argument("--nur-ocr", action="store_true", help="nur OCR-Sprachdaten laden")
    ap.add_argument("--nur-ki", action="store_true", help="nur KI-Programm und Modell laden")
    ap.add_argument("--ocr-best", action="store_true", help="genauere (langsamere) OCR-Modelle verwenden")
    ap.add_argument("--test", action="store_true", help="nur Selbsttest der KI ausführen")
    ap.add_argument("--quellen-pruefen", action="store_true",
                    help="nur prüfen, ob alle Download-Quellen erreichbar sind (lädt nichts)")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # Umlaute in der Windows-Konsole
        except (AttributeError, ValueError):
            pass
    print(f"Blackline 2 – Einrichtung\nProgrammordner: {paths.app_root()}")
    try:
        if args.test:
            return 0 if self_test() else 1
        if args.quellen_pruefen:
            return 0 if check_sources() else 1
        if not args.nur_ki:
            setup_ocr("best" if args.ocr_best else "standard")
        if not args.nur_ocr:
            setup_llama(args.gpu)
            setup_model(args.modell)
            self_test()
    except (KeyboardInterrupt, SetupCancelled):
        print("\nAbgebrochen. Ein erneuter Aufruf setzt angefangene Downloads fort.")
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"\nFEHLER: {exc}")
        return 1
    print("\nFertig. Blackline 2 kann jetzt gestartet werden.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
