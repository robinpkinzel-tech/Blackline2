import io
import socketserver
import threading
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from blackline2 import setup_ki


ASSETS = [{"name": n} for n in (
    "llama-b1-bin-win-cpu-x64.zip", "llama-b1-bin-win-vulkan-x64.zip", "llama-b1-bin-win-hip-radeon-x64.zip",
    "llama-b1-bin-win-cuda-12.4-x64.zip", "cudart-llama-bin-win-cuda-12.4-x64.zip",
    "llama-b1-bin-win-cpu-arm64.zip", "llama-b1-bin-macos-arm64.tar.gz", "llama-b1-bin-macos-x64.tar.gz",
    "llama-b1-bin-ubuntu-x64.tar.gz",
    "llama-b1-bin-ubuntu-vulkan-x64.tar.gz", "llama-b1-xcframework.zip", "nightly-tag.txt")]


def _pick(monkeypatch, plat, machine, gpu, assets=ASSETS):
    monkeypatch.setattr(setup_ki.sys, "platform", plat)
    monkeypatch.setattr(setup_ki.platform, "machine", lambda: machine)
    a = setup_ki.pick_asset(assets, gpu)
    return a and a["name"]


def test_pick_asset(monkeypatch):
    assert _pick(monkeypatch, "win32", "AMD64", "cpu") == "llama-b1-bin-win-cpu-x64.zip"
    assert _pick(monkeypatch, "win32", "AMD64", "vulkan") == "llama-b1-bin-win-vulkan-x64.zip"
    assert _pick(monkeypatch, "win32", "AMD64", "cuda") == "llama-b1-bin-win-cuda-12.4-x64.zip"
    assert _pick(monkeypatch, "win32", "ARM64", "cpu") == "llama-b1-bin-win-cpu-arm64.zip"
    assert _pick(monkeypatch, "darwin", "arm64", "cpu") == "llama-b1-bin-macos-arm64.tar.gz"
    # Intel-Mac bekommt das Intel-Paket (nicht das gleich gut bewertete Apple-Silicon-Paket) und umgekehrt
    assert _pick(monkeypatch, "darwin", "x86_64", "cpu") == "llama-b1-bin-macos-x64.tar.gz"
    both = [{"name": "llama-b1-bin-macos-arm64.tar.gz"}, {"name": "llama-b1-bin-macos-x64.tar.gz"}]
    assert _pick(monkeypatch, "darwin", "x86_64", "cpu", both) == "llama-b1-bin-macos-x64.tar.gz"
    assert _pick(monkeypatch, "darwin", "arm64", "cpu", both) == "llama-b1-bin-macos-arm64.tar.gz"
    assert _pick(monkeypatch, "darwin", "x86_64", "cpu", [both[0]]) is None  # kein Intel-Paket vorhanden
    assert _pick(monkeypatch, "darwin", "x86_64", "cpu", [{"name": "llama-b1-bin-macos.zip"}]) \
        == "llama-b1-bin-macos.zip"  # Paket ohne Architekturangabe bleibt erlaubt
    assert _pick(monkeypatch, "linux", "x86_64", "cpu") == "llama-b1-bin-ubuntu-x64.tar.gz"
    assert _pick(monkeypatch, "linux", "x86_64", "vulkan") == "llama-b1-bin-ubuntu-vulkan-x64.tar.gz"
    assert _pick(monkeypatch, "linux", "x86_64", "cpu", [{"name": "nightly-tag.txt"}]) is None


def test_find_asset_skips_release_without_binaries(monkeypatch):
    rels = [{"tag_name": "nightly", "assets": [{"name": "nightly-tag.txt"}]},
            {"tag_name": "b2", "assets": [{"name": "llama-b2-bin-ubuntu-x64.tar.gz"}]}]
    monkeypatch.setattr(setup_ki, "list_llama_releases", lambda: rels)
    monkeypatch.setattr(setup_ki.sys, "platform", "linux")
    monkeypatch.setattr(setup_ki.platform, "machine", lambda: "x86_64")
    rel, asset = setup_ki.find_llama_asset("cpu")
    assert rel["tag_name"] == "b2" and asset["name"] == "llama-b2-bin-ubuntu-x64.tar.gz"


def test_download_and_extract(tmp_path):
    src = tmp_path / "srv"
    src.mkdir()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("build/bin/llama-server", "#!/bin/sh\n")
    (src / "paket.zip").write_bytes(buf.getvalue())
    handler = partial(SimpleHTTPRequestHandler, directory=str(src))

    class Server(ThreadingHTTPServer):
        def server_bind(self):  # ohne langsame DNS-Auflösung
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = "localhost", self.server_address[1]

    httpd = Server(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/paket.zip"
        target = setup_ki.download(url, tmp_path / "dl" / "paket.zip", "paket.zip")
        assert target.read_bytes() == buf.getvalue()
        dest = tmp_path / "out"
        dest.mkdir()
        setup_ki._extract(target, dest)
        assert (dest / "build" / "bin" / "llama-server").exists()
    finally:
        httpd.shutdown()


def test_old_intel_mac_gets_own_llama_package(monkeypatch):
    """Intel-Mac mit macOS 12: das offizielle Paket (verlangt macOS 13.3) wäre unbrauchbar -> eigenes Paket."""
    monkeypatch.setattr(setup_ki.sys, "platform", "darwin")
    monkeypatch.setattr(setup_ki.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(setup_ki.platform, "mac_ver", lambda: ("12.7.6", ("", "", ""), "x86_64"))
    monkeypatch.setattr(setup_ki, "list_llama_releases", lambda: (_ for _ in ()).throw(AssertionError("kein Netz")))
    rel, asset = setup_ki.find_llama_asset("cpu")
    assert asset["name"] == "llama-cpp-macos12-x64.zip"
    assert asset["browser_download_url"].startswith("https://github.com/robinpkinzel-tech/Blackline2/releases/")

    # macOS 13.3 oder neuer, oder Apple-Chip: offizielles Paket
    rels = [{"tag_name": "b2", "assets": [{"name": "llama-b2-bin-macos-arm64.tar.gz"},
                                          {"name": "llama-b2-bin-macos-x64.tar.gz"}]}]
    monkeypatch.setattr(setup_ki, "list_llama_releases", lambda: rels)
    for ver in ("13.3", "14.0", "26.5.2"):
        monkeypatch.setattr(setup_ki.platform, "mac_ver", lambda ver=ver: (ver, ("", "", ""), "x86_64"))
        assert setup_ki.find_llama_asset("cpu")[1]["name"] == "llama-b2-bin-macos-x64.tar.gz"
    monkeypatch.setattr(setup_ki.platform, "mac_ver", lambda: ("12.7.6", ("", "", ""), "arm64"))
    monkeypatch.setattr(setup_ki.platform, "machine", lambda: "arm64")
    assert setup_ki.find_llama_asset("cpu")[1]["name"] == "llama-b2-bin-macos-arm64.tar.gz"
    assert setup_ki.macos_version() == (12, 7)
