"""Prüft, welche macOS-Version die Programmdateien (Mach-O) in einem Ordner mindestens verlangen.

    python packaging/check_macos_version.py "dist/Blackline 2.app" --max 12.0

Betrachtet wird nur der Teil für die Architektur dieses Rechners (oder --arch x86_64 / arm64).
Mit --max endet das Programm mit Fehler, wenn irgendeine Datei eine neuere macOS-Version verlangt –
so wird keine App veröffentlicht, die auf dem Zielrechner (z. B. iMac 2015 mit macOS 12) nicht startet.
"""

from __future__ import annotations

import argparse
import os
import platform
import struct
import sys

CPU = {"x86_64": 0x01000007, "arm64": 0x0100000C}
FAT_MAGICS = {0xCAFEBABE, 0xCAFEBABF}
MH_MAGIC_64 = 0xFEEDFACF
LC_BUILD_VERSION, LC_VERSION_MIN_MACOSX = 0x32, 0x24
PLATFORM_MACOS = 1


def fmt(v: int) -> str:
    return f"{v >> 16}.{(v >> 8) & 255}.{v & 255}"


def parse(text: str) -> int:
    parts = [int(x) for x in text.split(".")] + [0, 0]
    return (parts[0] << 16) | (parts[1] << 8) | parts[2]


def _slice_minos(f, base: int, cpu: int) -> int | None:
    f.seek(base)
    head = f.read(32)
    if len(head) < 32 or struct.unpack_from("<I", head, 0)[0] != MH_MAGIC_64:
        return None
    cputype, _sub, _ftype, ncmds, sizeofcmds = struct.unpack_from("<iiIII", head, 4)
    if cputype != cpu:
        return None
    cmds = f.read(sizeofcmds)
    pos, found = 0, None
    for _ in range(ncmds):
        if pos + 8 > len(cmds):
            break
        cmd, size = struct.unpack_from("<II", cmds, pos)
        if cmd == LC_BUILD_VERSION and struct.unpack_from("<I", cmds, pos + 8)[0] == PLATFORM_MACOS:
            found = struct.unpack_from("<I", cmds, pos + 12)[0]  # build_version_command: minos
        elif cmd == LC_VERSION_MIN_MACOSX:
            found = struct.unpack_from("<I", cmds, pos + 8)[0]
        if size < 8:
            break
        pos += size
    return found


def file_minos(path: str, arch: str) -> int | None:
    """Mindest-macOS dieser Datei für die Architektur – None, wenn keine passende Programmdatei."""
    cpu = CPU[arch]
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
            if len(magic) < 4:
                return None
            be = struct.unpack(">I", magic)[0]
            if be in FAT_MAGICS:
                n = struct.unpack(">I", f.read(4))[0]
                if n > 16:
                    return None
                table = f.read(20 * n) if be == 0xCAFEBABE else b""
                for i in range(n if be == 0xCAFEBABE else 0):
                    _cpu, _sub, offset, _size, _align = struct.unpack_from(">iiIII", table, 20 * i)
                    if _cpu == cpu:
                        return _slice_minos(f, offset, cpu)
                return None
            return _slice_minos(f, 0, cpu)
    except OSError:
        return None


def scan(folder: str, arch: str) -> list[tuple[int, str]]:
    out = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            path = os.path.join(root, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            v = file_minos(path, arch)
            if v is not None:
                out.append((v, os.path.relpath(path, folder)))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--arch", choices=list(CPU), default="arm64" if platform.machine() == "arm64" else "x86_64")
    ap.add_argument("--max", help="höchste erlaubte macOS-Version, z. B. 12.0")
    args = ap.parse_args(argv)
    found = scan(args.folder, args.arch)
    if not found:
        print(f"Keine Programmdateien für {args.arch} in {args.folder} gefunden.")
        return 2
    found.sort(reverse=True)
    print(f"{len(found)} Programmdateien ({args.arch}); höchste geforderte macOS-Version: {fmt(found[0][0])}")
    for v, name in found[:8]:
        print(f"  {fmt(v):8} {name}")
    if args.max and found[0][0] > parse(args.max):
        too_new = [n for v, n in found if v > parse(args.max)]
        print(f"FEHLER: {len(too_new)} Datei(en) verlangen mehr als macOS {args.max}.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
