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
import platform
import re
import shutil
import ssl
import stat
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
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


def get_json(url: str) -> object:
    with _open(url) as r:
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
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        now = time.monotonic()
                        if now - last > 0.5:
                            last = now
                            if total:
                                print(f"\r  {label}: {done / 2**20:,.0f} / {total / 2**20:,.0f} MB "
                                      f"({100 * done / total:4.1f} %)", end="", flush=True)
                            else:
                                print(f"\r  {label}: {done / 2**20:,.0f} MB", end="", flush=True)
            print()
            part.replace(target)
            return target
        except urllib.error.HTTPError as exc:
            if exc.code == 416:  # schon vollständig
                part.replace(target)
                return target
            raise
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            print(f"\n  Verbindungsproblem ({exc}) – neuer Versuch {attempt + 2}/5 …")
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Download von {label} nicht möglich.")


# ---------------------------------------------------------------- OCR

def setup_ocr(variant: str = "standard", langs: tuple[str, ...] = ("deu", "eng")) -> None:
    print("\n== Texterkennung (OCR-Sprachdaten) ==")
    target_dir = paths.tessdata_dir()
    for lang in langs:
        target = target_dir / f"{lang}.traineddata"
        if target.exists() and target.stat().st_size > 1_000_000:
            print(f"  {lang}.traineddata ist bereits vorhanden.")
            continue
        download(TESSDATA[variant].format(lang=lang), target, f"{lang}.traineddata")
    print(f"  OK – gespeichert in {target_dir}")


# ---------------------------------------------------------------- llama.cpp

def _asset_patterns(gpu: str) -> list[str]:
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if sys.platform == "win32":
        arch = "arm64" if arm else "x64"
        if gpu == "cuda":
            return [rf"bin-win-cuda-12[\d.]*-{arch}\.zip$", rf"bin-win-cuda-[\d.]+-{arch}\.zip$"]
        if gpu == "vulkan":
            return [rf"bin-win-vulkan-{arch}\.zip$"]
        return [rf"bin-win-cpu-{arch}\.zip$", rf"bin-win-avx2-{arch}\.zip$"]
    if sys.platform == "darwin":
        return [rf"bin-macos-{'arm64' if arm else 'x64'}\.(zip|tar\.gz)$"]
    arch = "arm64" if arm else "x64"
    if gpu == "vulkan":
        return [rf"bin-ubuntu-vulkan-{arch}\.(zip|tar\.gz)$"]
    return [rf"bin-ubuntu-{arch}\.(zip|tar\.gz)$"]


def pick_asset(assets: list[dict], gpu: str) -> dict | None:
    for pat in _asset_patterns(gpu):
        for a in assets:
            if re.search(pat, a.get("name", "")):
                return a
    return None


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


def setup_llama(gpu: str = "cpu") -> Path:
    print("\n== KI-Programm (llama.cpp / llama-server) ==")
    rel = get_json("https://api.github.com/repos/ggml-org/llama.cpp/releases/latest")
    assets = rel.get("assets", []) if isinstance(rel, dict) else []
    asset = pick_asset(assets, gpu)
    if asset is None:
        names = "\n    ".join(a.get("name", "") for a in assets)
        raise RuntimeError(f"Kein passendes Paket für dieses System gefunden. Verfügbar:\n    {names}")
    print(f"  Version {rel.get('tag_name')}: {asset['name']}")
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
    print(f"  OK – {server}")
    return server


# ---------------------------------------------------------------- Modell

def setup_model(choice: str) -> Path:
    info = MODELS[choice]
    print(f"\n== KI-Modell: {info['info']} ==")
    data = get_json(f"https://huggingface.co/api/models/{info['repo']}")
    files = [s["rfilename"] for s in data.get("siblings", [])] if isinstance(data, dict) else []
    cands = [f for f in files if re.search(info["pattern"], f, re.I) and "mmproj" not in f.lower()]
    if not cands:
        raise RuntimeError(f"Keine passende Modelldatei in {info['repo']} gefunden.")
    fname = sorted(cands, key=len)[0]
    target = paths.ki_models_dir() / Path(fname).name
    if target.exists():
        print(f"  {target.name} ist bereits vorhanden.")
    else:
        url = f"https://huggingface.co/{info['repo']}/resolve/main/{fname}?download=true"
        download(url, target, target.name)
    # andere Modelle nicht löschen, aber das neue als Standard eintragen
    from blackline2.settings import Settings

    s = Settings.load()
    s.model_path = str(target)
    s.save()
    print(f"  OK – {target}")
    return target


# ---------------------------------------------------------------- Selbsttest

def self_test() -> bool:
    from blackline2.ai.client import ChatClient
    from blackline2.ai.detector import AIDetector
    from blackline2.ai.server import LocalAIServer
    from blackline2.detect_inputs import UserInputs
    from blackline2.labels import PersonRegistry
    from blackline2.model import PageData, Word
    from blackline2.settings import Settings

    print("\n== Selbsttest der KI ==")
    s = Settings.load()
    server = Path(s.llama_server_path) if s.llama_server_path else paths.find_llama_server()
    model = Path(s.model_path) if s.model_path else paths.find_model()
    if not server or not model:
        print("  KI ist noch nicht eingerichtet.")
        return False
    srv = LocalAIServer(server, model, s.ki_context, s.ki_threads, s.ki_gpu_layers, s.ki_extra_args)
    t0 = time.monotonic()
    try:
        srv.start()
        print("  Modell wird geladen …")
        srv.wait_ready(s.ki_start_timeout)
        print(f"  geladen nach {time.monotonic() - t0:.0f} s")
        text = ("Sehr geehrte Frau Petra Musterfrau, wie mit Herrn Kinzel besprochen, "
                "überweisen Sie bitte an Herrn Jens Beispiel, Bahnhofstraße 7, 35578 Wetzlar.")
        page = PageData(0, 595, 842, [Word(t, (0, 0, 1, 1), 0) for t in text.split()])
        det = AIDetector(ChatClient(srv.base_url, srv.api_key, timeout=600), PersonRegistry(),
                         UserInputs(mandant_name="Robin Kinzel"))
        t1 = time.monotonic()
        found = det.analyze_page(page, 1, 1)
        print(f"  Antwort nach {time.monotonic() - t1:.0f} s:")
        for f in found:
            print(f"    - {f.kategorie:12} {f.text!r:32} ({f.bezug})")
        return bool(found)
    except Exception as exc:  # noqa: BLE001
        print(f"  FEHLER: {exc}")
        print("  Letzte Meldungen des KI-Servers:\n    " + "\n    ".join(list(srv.log)[-10:]))
        return False
    finally:
        srv.stop()
        print("  KI wieder beendet.")


def check_sources() -> bool:
    ok = True
    rel = get_json("https://api.github.com/repos/ggml-org/llama.cpp/releases/latest")
    assets = rel.get("assets", []) if isinstance(rel, dict) else []
    for gpu in ("cpu", "vulkan", "cuda"):
        a = pick_asset(assets, gpu)
        print(f"  llama.cpp {gpu:6}: {a['name'] if a else 'NICHT GEFUNDEN'}")
        ok &= a is not None or gpu != "cpu"
    for key, info in MODELS.items():
        data = get_json(f"https://huggingface.co/api/models/{info['repo']}")
        files = [s["rfilename"] for s in data.get("siblings", [])] if isinstance(data, dict) else []
        cands = [f for f in files if re.search(info["pattern"], f, re.I) and "mmproj" not in f.lower()]
        print(f"  Modell {key:10}: {cands[0] if cands else 'NICHT GEFUNDEN'}")
        ok &= bool(cands)
    for variant, url in TESSDATA.items():
        try:
            with _open(url.format(lang="deu"), {"Range": "bytes=0-99"}):
                print(f"  OCR {variant:8}: erreichbar")
        except Exception as exc:  # noqa: BLE001
            print(f"  OCR {variant:8}: FEHLER {exc}")
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
    except KeyboardInterrupt:
        print("\nAbgebrochen. Ein erneuter Aufruf setzt angefangene Downloads fort.")
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"\nFEHLER: {exc}")
        return 1
    print("\nFertig. Blackline 2 kann jetzt gestartet werden.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
