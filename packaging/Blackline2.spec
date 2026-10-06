# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-Bauanleitung für Blackline 2.

    pyinstaller --noconfirm packaging/Blackline2.spec

Ergebnis: dist/Blackline 2/ (Windows, Linux) bzw. dist/Blackline 2.app (macOS).
KI-Modell und OCR-Daten sind NICHT enthalten – die App lädt sie beim ersten
Start in den Benutzerordner (Menü „Datei → KI einrichten“).
"""

import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON_DIR = os.path.join(SPECPATH, "build")
icon = None
if sys.platform == "darwin" and os.path.exists(os.path.join(ICON_DIR, "icon.icns")):
    icon = os.path.join(ICON_DIR, "icon.icns")
elif sys.platform == "win32" and os.path.exists(os.path.join(ICON_DIR, "icon.ico")):
    icon = os.path.join(ICON_DIR, "icon.ico")

sys.path.insert(0, ROOT)
from blackline2 import __version__  # noqa: E402

a = Analysis(
    [os.path.join(ROOT, "blackline2", "__main__.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=collect_submodules("blackline2") + ["docx"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter", "unittest", "pydoc", "pytest",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
        "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras", "PySide6.Qt3DInput",
        "PySide6.Qt3DLogic", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtBluetooth",
        "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSerialPort",
        "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtTest", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
        "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
        "PySide6.QtSql", "PySide6.QtXml", "PySide6.QtNetworkAuth", "PySide6.QtWebSockets", "PySide6.QtWebChannel",
        "PySide6.QtHttpServer", "PySide6.QtTextToSpeech", "PySide6.QtSpatialAudio", "PySide6.QtGraphs",
        "PySide6.QtStateMachine", "PySide6.QtConcurrent", "PySide6.QtQuickWidgets", "PySide6.QtQuickControls2",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Blackline 2",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Blackline 2")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Blackline 2.app",
        icon=icon,
        bundle_identifier="de.blackline2.app",
        version=__version__,
        info_plist={
            "CFBundleDisplayName": "Blackline 2",
            "CFBundleShortVersionString": __version__,
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "Blackline 2 – lokale KI-Schwärzung",
            "LSApplicationCategoryType": "public.app-category.productivity",
            "LSMinimumSystemVersion": "12.0",
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "PDF",
                "CFBundleTypeRole": "Viewer",
                "LSItemContentTypes": ["com.adobe.pdf"],
            }],
        },
    )
