"""Lädt das für diesen Rechner passende llama.cpp-Paket und zeigt, welche macOS-Version es verlangt.

Nur für den Bau der Intel-Mac-Version: Die KI muss auf dem Zielrechner (macOS 12) starten können.
    python packaging/check_llama_cpp.py --max 12.0
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_macos_version  # noqa: E402
from blackline2 import setup_ki  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", help="höchste erlaubte macOS-Version")
    args = ap.parse_args()
    rel, asset = setup_ki.find_llama_asset("cpu")
    print(f"llama.cpp {rel.get('tag_name')}: {asset['name']}")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        archive = setup_ki.download(asset["browser_download_url"], tmp_path / asset["name"], asset["name"])
        dest = tmp_path / "paket"
        dest.mkdir()
        setup_ki._extract(archive, dest)
        return check_macos_version.main([str(dest)] + (["--max", args.max] if args.max else []))


if __name__ == "__main__":
    sys.exit(main())
