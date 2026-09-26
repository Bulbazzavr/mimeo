"""Манифест установки одним файлом (`Z-76`, `config/runtime.json`, `tools/runtime.py`).

Скачивать гигабайты тест не будет: он держит то, что ломается опечаткой, —
у каждого файла адрес, размер и сумма; каждый сервер запускается файлом, который
ставит установка, и берёт веса, которые она кладёт; загрузка сверяет сумму и
продолжает оборванное.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import runtime                                                 # noqa: E402


def test_every_item_has_address_size_and_sum():
    manifest = runtime.load()
    for item in manifest["items"]:
        assert item["url"].startswith("https://"), item["name"]
        assert isinstance(item["size"], int) and item["size"] > 0, item["name"]
        assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"]), item["name"]
        assert ("path" in item) != ("unzip_to" in item), f"{item['name']}: файл или архив"
        if "unzip_to" in item:
            assert item["check"].startswith(item["unzip_to"] + "/"), item["name"]


def test_servers_use_what_the_install_puts():
    """Сервер запускается установленным файлом и берёт установленные веса."""
    manifest = runtime.load()
    placed = {i.get("path") or i["check"] for i in manifest["items"]}
    folders = {i["unzip_to"] for i in manifest["items"] if "unzip_to" in i}
    for server in manifest["servers"]:
        assert server["exe"].split("/")[0] in folders, server["name"]
        weights = [a.replace("{root}/", "") for a in server["args"] if a.startswith("{root}/")]
        assert weights and set(weights) <= placed, server["name"]
        assert server["ready"].startswith("http://127.0.0.1:"), server["name"]


def test_ports_match_the_engine_configs():
    """Порты серверов — те же, что зовёт движок: config/model.json и generator.json."""
    from mimeo.plan import client, images

    manifest = runtime.load()
    readies = " ".join(s["ready"] for s in manifest["servers"])
    assert client.load_config().endpoint.base_url.replace("/v1", "") in readies
    assert images.load_config().base_url in readies


class _Files:
    """Отдаёт один файл и понимает Range — как GitHub и Hugging Face."""

    def __init__(self, blob: bytes):
        blob_ref = blob

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):              # noqa: N802
                start = 0
                rng = self.headers.get("Range")
                if rng:
                    start = int(rng.split("=")[1].split("-")[0])
                body = blob_ref[start:]
                self.send_response(206 if rng else 200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/file.bin"

    def close(self):
        self._server.shutdown()
        self._server.server_close()


def test_download_resumes_and_checks_the_sum(tmp_path, monkeypatch):
    for name in ("HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(name, raising=False)
    blob = os.urandom(3 * runtime.CHUNK + 123)
    good = hashlib.sha256(blob).hexdigest()
    files = _Files(blob)
    try:
        target = tmp_path / "w" / "file.bin"
        target.parent.mkdir()
        (tmp_path / "w" / "file.bin.part").write_bytes(blob[: runtime.CHUNK])   # обрыв
        runtime.download(files.url, str(target), len(blob), good)
        assert target.read_bytes() == blob and not (tmp_path / "w" / "file.bin.part").exists()

        with pytest.raises(RuntimeError, match="sha256"):
            runtime.download(files.url, str(tmp_path / "bad.bin"), len(blob), "0" * 64)
        assert not (tmp_path / "bad.bin").exists() and not (tmp_path / "bad.bin.part").exists()
    finally:
        files.close()
