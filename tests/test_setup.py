import io
import threading
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from blackline2 import setup_ki


def test_pick_asset_windows(monkeypatch):
    assets = [{"name": n} for n in (
        "llama-b1-bin-win-cpu-x64.zip", "llama-b1-bin-win-vulkan-x64.zip",
        "llama-b1-bin-win-cuda-12.4-x64.zip", "cudart-llama-bin-win-cuda-12.4-x64.zip",
        "llama-b1-bin-macos-arm64.zip", "llama-b1-bin-ubuntu-x64.zip")]
    monkeypatch.setattr(setup_ki.sys, "platform", "win32")
    monkeypatch.setattr(setup_ki.platform, "machine", lambda: "AMD64")
    assert setup_ki.pick_asset(assets, "cpu")["name"] == "llama-b1-bin-win-cpu-x64.zip"
    assert setup_ki.pick_asset(assets, "vulkan")["name"] == "llama-b1-bin-win-vulkan-x64.zip"
    assert setup_ki.pick_asset(assets, "cuda")["name"] == "llama-b1-bin-win-cuda-12.4-x64.zip"
    monkeypatch.setattr(setup_ki.sys, "platform", "darwin")
    monkeypatch.setattr(setup_ki.platform, "machine", lambda: "arm64")
    assert setup_ki.pick_asset(assets, "cpu")["name"] == "llama-b1-bin-macos-arm64.zip"


def test_download_and_extract(tmp_path):
    src = tmp_path / "srv"
    src.mkdir()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("build/bin/llama-server", "#!/bin/sh\n")
    (src / "paket.zip").write_bytes(buf.getvalue())
    handler = partial(SimpleHTTPRequestHandler, directory=str(src))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
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
