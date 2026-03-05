import re
import struct
import threading
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, TextIO


_CHAT_STATS = {
    "total": 0,
    "chat": 0,
    "types": Counter(),
}


def unescape(value: str) -> str:
    return value.replace("@A", "@").replace("@S", "/")


def parse_kv(message: str) -> dict:
    fields = message.split("/")
    result = {}
    for item in fields:
        if "@=" not in item:
            continue
        key, value = item.split("@=", 1)
        result[key] = unescape(value)
    return result


def fetch_room_info(room_url: str, log: Optional[Callable[[str], None]] = None) -> dict:
    try:
        with urllib.request.urlopen(room_url, timeout=10) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        if log:
            log(f"fetch room info failed: {exc}")
        return {"host": "", "title": "", "live_time": ""}

    title = ""
    h1_match = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
    if h1_match:
        title = h1_match.group(1).strip()

    host = ""
    og_match = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
    if og_match:
        og_text = og_match.group(1)
        parts = og_text.split('_')
        if len(parts) >= 3:
            host = parts[2].replace('直播', '').strip()

    live_time = ""
    show_time_match = re.search(r'"show_time"\s*:\s*"([^"]+)"', html)
    if show_time_match:
        live_time = show_time_match.group(1)

    if log:
        log(f"Fetched room info - host: {host}, title: {title}, live_time: {live_time}")
    return {"host": host, "title": title, "live_time": live_time}


def _format_chat_lines(text: str, allowed_types: set[str], formatter: Callable[[dict], str], debug: bool = False, log: Optional[Callable[[str], None]] = None) -> list[str]:
    stats = _CHAT_STATS
    lines: list[str] = []

    for msg in text.split("\x00"):
        if not msg:
            continue

        data = parse_kv(msg)
        msg_type = data.get("type", "")
        if msg_type:
            stats["types"][msg_type] += 1
        stats["total"] += 1

        if msg_type not in allowed_types:
            continue

        stats["chat"] += 1
        lines.append(formatter(data))

    if debug and stats["total"] % 200 == 0 and log:
        top_types = stats["types"].most_common(5)
        log(f"parsed total={stats['total']} chat={stats['chat']} top_types={top_types}")

    return lines


def format_chat_json_lines(text: str, debug: bool = False, log: Optional[Callable[[str], None]] = None) -> list[str]:
    import json

    def formatter(data: dict) -> str:
        try:
            return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        except Exception as exc:
            return json.dumps({"error": str(exc), "raw": str(data)})

    return _format_chat_lines(
        text,
        allowed_types={"chatmsg", "hischatmsg"},
        formatter=formatter,
        debug=debug,
        log=log,
    )


def format_chat_text_lines(text: str, debug: bool = False, log: Optional[Callable[[str], None]] = None) -> list[str]:
    def formatter(data: dict) -> str:
        user = data.get("nn", "")
        content = data.get("txt", "")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"[{timestamp}] {user}: {content}"

    return _format_chat_lines(
        text,
        allowed_types={"chatmsg", "hischatmsg"},
        formatter=formatter,
        debug=debug,
        log=log,
    )


def parse_douyu_packets(
    buffer: bytearray,
    data: bytes,
    line_parser: Callable[[str], list[str]],
) -> list[str]:
    buffer.extend(data)
    lines: list[str] = []

    while len(buffer) >= 12:
        length = struct.unpack("<I", buffer[:4])[0]
        if length > 0x100000:
            del buffer[:]
            break

        packet_size = length + 4
        if len(buffer) < packet_size:
            break

        packet = buffer[12:packet_size - 1]
        del buffer[:packet_size]

        try:
            text = packet.decode("utf-8", errors="replace")
        except UnicodeDecodeError:
            continue

        lines.extend(line_parser(text))

    return lines


def sanitize_filename_part(value: str) -> str:
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


def create_danmaku_writers(
    room_id: str,
    room_info: dict,
    out_dir: str,
    batch_size: int,
    cwd: Path,
) -> tuple[BatchWriter, SingleFileWriter]:
    fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = (
        f"# room_id={room_id} host={room_info.get('host', '')} "
        f"title={room_info.get('title', '')} live_time={room_info.get('live_time', '')} "
        f"fetch_time={fetch_time}"
    )

    file_tag = "_".join(
        part
        for part in [
            sanitize_filename_part(room_info.get("host", "")),
            sanitize_filename_part(room_info.get("title", "")),
        ]
        if part
    )

    batch_writer = BatchWriter(Path(out_dir), room_id, batch_size, header, file_tag)
    single_writer = SingleFileWriter(cwd / f"danmaku_{room_id}.txt", header)
    return batch_writer, single_writer
