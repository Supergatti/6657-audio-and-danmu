import argparse
import base64
import asyncio
import re
import struct
import threading
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional, Set, TextIO

import websockets
from websockets.asyncio.server import ServerConnection as WebSocketServerProtocol
from websockets.http11 import Request, Response
from websockets.datastructures import Headers

ROOM_URL = "https://www.douyu.com/6979222"
BATCH_SIZE = 2000
OUT_DIR = "danmaku_logs"
DEBUG = True
WS_HOST = "127.0.0.1"
WS_PORT = 8765
WEB_ROOT = Path(__file__).parent / "web"
INDEX_FILE = WEB_ROOT / "index.html"


def _log(msg: str) -> None:
    if DEBUG:
        print(f"[DEBUG {datetime.now():%H:%M:%S}] {msg}")


def _unescape(value: str) -> str:
    return value.replace("@A", "@").replace("@S", "/")


def _parse_kv(message: str) -> dict:
    fields = message.split("/")
    result = {}
    for item in fields:
        if "@=" not in item:
            continue
        key, value = item.split("@=", 1)
        result[key] = _unescape(value)
    return result


def _fetch_room_info(room_url: str) -> dict:
    try:
        with urllib.request.urlopen(room_url, timeout=10) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        _log(f"fetch room info failed: {exc}")
        return {"host": "", "title": "", "live_time": ""}

    # Extract room title from h1 tag
    title = ""
    h1_match = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
    if h1_match:
        title = h1_match.group(1).strip()
    
    # Extract host name from og:title meta tag
    host = ""
    og_match = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
    if og_match:
        og_text = og_match.group(1)
        parts = og_text.split('_')
        if len(parts) >= 3:
            host = parts[2].replace('直播', '').strip()
    
    # Try to find live time from JSON data
    live_time = ""
    show_time_match = re.search(r'"show_time"\s*:\s*"([^"]+)"', html)
    if show_time_match:
        live_time = show_time_match.group(1)
    
    _log(f"Fetched room info - host: {host}, title: {title}, live_time: {live_time}")
    return {"host": host, "title": title, "live_time": live_time}


def _sanitize_filename_part(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]", "_", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


class BatchWriter:
    def __init__(self, out_dir: Path, room_id: str, batch_size: int, header: str, file_tag: str) -> None:
        self.out_dir = out_dir
        self.room_id = room_id
        self.batch_size = batch_size
        self.header = header
        self.file_tag = file_tag
        self.current_file: Optional[TextIO] = None
        self.current_count = 0
        self.file_index = 0

    def _open_next_file(self) -> None:
        if self.current_file:
            self.current_file.close()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.file_index += 1
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        tag = f"_{self.file_tag}" if self.file_tag else ""
        filename = f"room_{self.room_id}{tag}_{timestamp}_{self.file_index:04d}.log"
        self.current_file = (self.out_dir / filename).open("a", encoding="utf-8")
        self.current_count = 0
        if self.header:
            self.current_file.write(self.header + "\n")
            self.current_file.flush()

    def write_line(self, line: str) -> None:
        if not self.current_file or self.current_count >= self.batch_size:
            self._open_next_file()
        if self.current_file:
            self.current_file.write(line + "\n")
            self.current_file.flush()
            self.current_count += 1

    def close(self) -> None:
        if self.current_file:
            self.current_file.close()
            self.current_file = None


class SingleFileWriter:
    def __init__(self, file_path: Path, header: str) -> None:
        self.file_path = file_path
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._file = self.file_path.open("a", encoding="utf-8")
        if header:
            self._file.write(header + "\n")
            self._file.flush()

    def write_line(self, line: str) -> None:
        with self._lock:
            self._file.write(line + "\n")
            self._file.flush()

    def close(self) -> None:
        with self._lock:
            self._file.close()


def _format_chat_lines(text: str) -> list[str]:
    if not hasattr(_format_chat_lines, "_debug_count"):
        _format_chat_lines._debug_count = 0  # type: ignore[attr-defined]
        _format_chat_lines._stats = {  # type: ignore[attr-defined]
            "total": 0,
            "chat": 0,
            "types": Counter(),
        }
    stats = _format_chat_lines._stats  # type: ignore[attr-defined]
    lines = []
    for msg in text.split("\x00"):
        if not msg:
            continue
        data = _parse_kv(msg)
        msg_type = data.get("type", "")
        if msg_type:
            stats["types"][msg_type] += 1
        stats["total"] += 1

        if msg_type not in {"chatmsg", "hischatmsg"}:
            continue
        stats["chat"] += 1
        user = data.get("nn", "")
        content = data.get("txt", "")
        if DEBUG and _format_chat_lines._debug_count < 3:  # type: ignore[attr-defined]
            _format_chat_lines._debug_count += 1  # type: ignore[attr-defined]
            preview_keys = list(data.keys())[:20]
            _log(f"chat payload keys={preview_keys}, txt={data.get('txt')}")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"[{timestamp}] {user}: {content}")

    if DEBUG and stats["total"] % 200 == 0:
        top_types = stats["types"].most_common(5)
        _log(f"parsed total={stats['total']} chat={stats['chat']} top_types={top_types}")
    return lines


def _parse_douyu_packets(buffer: bytearray, data: bytes) -> list[str]:
    buffer.extend(data)
    lines: list[str] = []
    while len(buffer) >= 12:
        length = struct.unpack("<I", buffer[:4])[0]
        # Allow some flexibility for protocol variations
        if length > 0x100000: # Sanity check for corruption
             del buffer[:]
             break
        packet_size = length + 4
        if len(buffer) < packet_size:
            break
        packet = buffer[12:packet_size - 1]
        del buffer[:packet_size]
        try:
            # Use 'replace' to debug potential encoding issues with Emoji
            text = packet.decode("utf-8", errors="replace")
        except UnicodeDecodeError:
            continue
        lines.extend(_format_chat_lines(text))
    return lines


async def _broadcast_loop(queue: asyncio.Queue, clients: Set[WebSocketServerProtocol]) -> None:
    while True:
        line = await queue.get()
        dead = []
        for ws in clients:
            try:
                await ws.send(line)
            except OSError:
                dead.append(ws)
        for ws in dead:
            clients.discard(ws)


async def _ws_handler(ws: WebSocketServerProtocol, clients: Set[WebSocketServerProtocol]) -> None:
    clients.add(ws)
    _log("ws client connected")
    try:
        async for _ in ws:
            pass
    finally:
        clients.discard(ws)
        _log("ws client disconnected")


def _load_index_html(room_id: str) -> bytes:
    if not INDEX_FILE.exists():
        return b"index.html not found"
    template = INDEX_FILE.read_text(encoding="utf-8")
    html = template.replace("{{ROOM_ID}}", room_id)
    return html.encode("utf-8")


def _process_request_factory(room_id: str):
    index_bytes = _load_index_html(room_id)

    def _process_request(connection: WebSocketServerProtocol, request: Request) -> Response:
        path = request.path
        if path == "/" or path.startswith("/?") or path.startswith("/index.html"):
            return Response(
                status_code=200,
                reason_phrase="OK",
                headers=Headers([
                    ("Content-Type", "text/html; charset=utf-8"),
                    ("Content-Length", str(len(index_bytes))),
                ]),
                body=index_bytes,
            )
        body = b"Not Found"
        return Response(
            status_code=404,
            reason_phrase="Not Found",
            headers=Headers([("Content-Type", "text/plain"), ("Content-Length", str(len(body)))]),
            body=body,
        )

    return _process_request


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Douyu danmaku relay server.")
    parser.add_argument("--room-url", default=ROOM_URL, help="Douyu room url.")
    parser.add_argument("--room-id", default=None, help="Numeric room id override.")
    parser.add_argument("--host", default=WS_HOST, help="Bind host.")
    parser.add_argument("--port", type=int, default=WS_PORT, help="Bind port.")
    parser.add_argument("--out-dir", default=OUT_DIR, help="Log output directory.")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="Lines per log file.")
    parser.add_argument("--debug", action="store_true", default=DEBUG, help="Enable debug logs.")
    parser.add_argument("--headed", action="store_true", help="Show browser window (headless by default).")
    return parser.parse_args()


async def _run_official_client(room_url: str, on_message, stop_event: asyncio.Event, headless: bool) -> None:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # pragma: no cover - import guard
        raise RuntimeError("Playwright is required for --official.") from exc

    buffer = bytearray()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        page = await browser.new_page()

        def handle_ws(ws):
            _log(f"websocket opened: {ws.url}")
            frame_log_count = {"count": 0}
            # Each WebSocket must have its own buffer to avoid race conditions
            ws_buffer = bytearray()

            def on_frame(frame):
                if DEBUG and frame_log_count["count"] < 5:
                    frame_log_count["count"] += 1
                    frame_repr = repr(frame)
                    if len(frame_repr) > 200:
                        frame_repr = frame_repr[:200] + "..."
                    _log(f"ws frame type={type(frame)} repr={frame_repr}")

                payload = None
                opcode = None
                if isinstance(frame, (str, bytes, bytearray)):
                    payload = frame
                elif isinstance(frame, dict):
                    payload = frame.get("payload")
                    opcode = frame.get("opcode")
                else:
                    payload = getattr(frame, "payload", None)
                    opcode = getattr(frame, "opcode", None)

                if DEBUG:
                    payload_len = len(payload) if payload is not None else 0
                    if payload_len > 0: # Reduce noise
                        _log(f"ws frame received: opcode={opcode}, len={payload_len}")

                if isinstance(payload, str):
                    if opcode == 2:
                        try:
                            binary_payload = base64.b64decode(payload, validate=True)
                        except ValueError:
                            _log("ws frame base64 decode failed")
                            return
                        for line in _parse_douyu_packets(ws_buffer, binary_payload):
                            on_message(line)
                        return
                    text_lines = _format_chat_lines(payload)
                    if text_lines:
                        for line in text_lines:
                            on_message(line)
                        return
                    if opcode is None:
                        try:
                            binary_payload = base64.b64decode(payload, validate=True)
                        except ValueError:
                             # Not all string payloads are base64, so ignore error
                            return
                        for line in _parse_douyu_packets(ws_buffer, binary_payload):
                            on_message(line)
                        return

                if isinstance(payload, (bytes, bytearray)):
                    for line in _parse_douyu_packets(ws_buffer, bytes(payload)):
                        on_message(line)

            ws.on("framereceived", on_frame)
            ws.on("close", lambda: _log("websocket closed"))

        page.on("websocket", handle_ws)
        await page.goto(room_url, wait_until="domcontentloaded")
        _log("official page loaded")

        await stop_event.wait()
        await browser.close()


async def main_async() -> None:
    args = _parse_args()
    global DEBUG
    DEBUG = args.debug

    room_id = args.room_id
    match = None
    if not room_id:
        match = re.search(r"(\d+)", args.room_url)
        if not match:
            raise ValueError("ROOM_URL must contain a numeric room id.")
        room_id = match.group(1)
    
    _log(f"room_id={room_id} starting official client")

    room_info = _fetch_room_info(args.room_url)
    fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = (
        f"# room_id={room_id} host={room_info.get('host','')} "
        f"title={room_info.get('title','')} live_time={room_info.get('live_time','')} "
        f"fetch_time={fetch_time}"
    )

    file_tag = "_".join(
        part for part in [
            _sanitize_filename_part(room_info.get("host", "")),
            _sanitize_filename_part(room_info.get("title", "")),
        ]
        if part
    )

    writer = BatchWriter(Path(args.out_dir), room_id, args.batch_size, header, file_tag)
    single_file = Path.cwd() / f"danmaku_{room_id}.txt"
    single_writer = SingleFileWriter(single_file, header)
    queue: asyncio.Queue = asyncio.Queue()
    clients: Set[WebSocketServerProtocol] = set()

    loop = asyncio.get_running_loop()

    def on_message(line: str) -> None:
        print(line)
        writer.write_line(line)
        single_writer.write_line(line)
        loop.call_soon_threadsafe(queue.put_nowait, line)

    official_stop = asyncio.Event()
    official_task = asyncio.create_task(
        _run_official_client(args.room_url, on_message, official_stop, not args.headed)
    )

    process_request = _process_request_factory(room_id)
    server = await websockets.serve(
        lambda ws: _ws_handler(ws, clients),
        args.host,
        args.port,
        process_request=process_request,
    )
    _log(f"ws server listening on ws://{args.host}:{args.port}")

    try:
        await _broadcast_loop(queue, clients)
    finally:
        server.close()
        await server.wait_closed()
        official_stop.set()
        if official_task:
            await official_task
        writer.close()
        single_writer.close()


if __name__ == "__main__":
    asyncio.run(main_async())
