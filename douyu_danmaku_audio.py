import argparse
import asyncio
import base64
import json
import logging
import re
import struct
import sys
import time
import urllib.request
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Set

import websockets
from websockets.asyncio.server import serve, ServerConnection
from websockets.http11 import Request, Response
from websockets.datastructures import Headers

# 屏蔽websockets库的所有日志输出
logging.getLogger('websockets').setLevel(logging.CRITICAL)
logging.getLogger('websockets.server').setLevel(logging.CRITICAL)
logging.getLogger('websockets.protocol').setLevel(logging.CRITICAL)
logging.getLogger('websockets.asyncio.server').setLevel(logging.CRITICAL)
logging.getLogger('websockets.asyncio').setLevel(logging.CRITICAL)

# 配置根logger，避免未捕获的异常打印
logging.basicConfig(
    level=logging.CRITICAL,
    format='%(message)s',
    stream=sys.stderr
)

ROOM_URL = "https://www.douyu.com/6979222"
WS_HOST = "127.0.0.1"
WS_PORT = 8766
MAX_DANMAKU_CACHE = 500
AUDIO_CACHE_SECONDS = 45
AUDIO_CHUNK_MS = 1000
DEBUG = False
WEB_ROOT = Path(__file__).parent / "web"


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
        if length > 0x100000:  # Sanity check for corruption
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
        lines.extend(_format_chat_lines(text))
    return lines


class DanmakuCache:
    """弹幕缓存，超过指定数量自动清空"""
    def __init__(self, max_size: int = MAX_DANMAKU_CACHE) -> None:
        self.max_size = max_size
        self.cache = deque(maxlen=max_size)
        self.count = 0

    def add(self, danmaku: str) -> None:
        self.cache.append(danmaku)
        self.count += 1
        if self.count >= self.max_size:
            _log(f"弹幕缓存达到{self.max_size}条，清空缓存")
            self.cache.clear()
            self.count = 0

    def get_recent(self, n: int = 50) -> list:
        """获取最近n条弹幕"""
        return list(self.cache)[-n:]


@dataclass
class AudioSegment:
    seg_id: int
    pts: float
    wall_time: float
    mime: str
    b64: str
    duration_ms: int

    def to_message(self) -> dict[str, Any]:
        return {
            "type": "audio_segment",
            "seg_id": self.seg_id,
            "pts": self.pts,
            "ts": self.wall_time,
            "mime": self.mime,
            "duration_ms": self.duration_ms,
            "data": self.b64,
        }


class AudioCache:
    def __init__(self, max_seconds: int = AUDIO_CACHE_SECONDS) -> None:
        self.max_seconds = max_seconds
        self.cache: Deque[AudioSegment] = deque()

    def add(self, segment: AudioSegment) -> None:
        self.cache.append(segment)
        min_pts = segment.pts - self.max_seconds
        while self.cache and self.cache[0].pts < min_pts:
            self.cache.popleft()

    def get_recent(self, seconds: int = 8) -> list[dict[str, Any]]:
        if not self.cache:
            return []
        latest = self.cache[-1].pts
        threshold = latest - seconds
        return [s.to_message() for s in self.cache if s.pts >= threshold]

    def status(self) -> dict[str, Any]:
        if not self.cache:
            return {"segments": 0, "latest_pts": 0.0}
        return {
            "segments": len(self.cache),
            "latest_pts": self.cache[-1].pts,
        }


class SyncState:
    def __init__(self) -> None:
        self.manual_offset_ms = 0
        self.last_sync_report: dict[str, Any] = {}


async def _safe_send(ws: ServerConnection, text: str, send_locks: dict[ServerConnection, asyncio.Lock]) -> bool:
    lock = send_locks.get(ws)
    if lock is None:
        return False
    try:
        async with lock:
            await ws.send(text)
        return True
    except Exception as exc:
        _log(f"发送失败: {exc}")
        return False


def _to_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


async def _broadcast_loop(
    queue: asyncio.Queue,
    clients: Set[ServerConnection],
    send_locks: dict[ServerConnection, asyncio.Lock],
    danmaku_cache: DanmakuCache,
    audio_cache: AudioCache,
    sync_state: SyncState,
) -> None:
    """广播事件到所有连接客户端"""
    _log("广播循环已启动")
    while True:
        event: dict[str, Any] = await queue.get()
        event_type = event.get("type")

        if event_type == "danmaku":
            text = event.get("text")
            if isinstance(text, str):
                danmaku_cache.add(text)
        elif event_type == "audio_segment":
            seg = AudioSegment(
                seg_id=int(event["seg_id"]),
                pts=float(event["pts"]),
                wall_time=float(event["ts"]),
                mime=str(event.get("mime") or "audio/webm;codecs=opus"),
                b64=str(event.get("data") or ""),
                duration_ms=int(event.get("duration_ms") or AUDIO_CHUNK_MS),
            )
            audio_cache.add(seg)

        payload = _to_json(event)
        dead = []
        for ws in list(clients):
            ok = await _safe_send(ws, payload, send_locks)
            if not ok:
                dead.append(ws)
        for ws in dead:
            clients.discard(ws)
            send_locks.pop(ws, None)

        if event_type == "audio_segment" and clients and DEBUG:
            _log(f"已广播音频分段 seg={event.get('seg_id')} 在线={len(clients)} offset={sync_state.manual_offset_ms}ms")


async def _ws_handler(
    ws: ServerConnection,
    clients: Set[ServerConnection],
    send_locks: dict[ServerConnection, asyncio.Lock],
    danmaku_cache: DanmakuCache,
    audio_cache: AudioCache,
    sync_state: SyncState,
) -> None:
    clients.add(ws)
    send_locks[ws] = asyncio.Lock()
    client_id = id(ws)
    _log(f"WebSocket客户端连接 [ID:{client_id}]，当前连接数: {len(clients)}")

    try:
        hello = {
            "type": "hello",
            "server_time": time.time(),
            "manual_offset_ms": sync_state.manual_offset_ms,
            "audio": audio_cache.status(),
        }
        await _safe_send(ws, _to_json(hello), send_locks)

        for danmaku in danmaku_cache.get_recent(20):
            await _safe_send(
                ws,
                _to_json({"type": "danmaku", "text": danmaku, "ts": time.time()}),
                send_locks,
            )
        for seg in audio_cache.get_recent(8):
            await _safe_send(ws, _to_json(seg), send_locks)

        async for raw_message in ws:
            try:
                data = json.loads(raw_message)
            except Exception:
                continue

            msg_type = data.get("type")
            if msg_type == "ping":
                await _safe_send(ws, _to_json({"type": "pong", "server_time": time.time()}), send_locks)
                continue

            if msg_type != "control":
                continue

            cmd = data.get("cmd")
            args = data.get("args") or {}

            if cmd == "get_status":
                reply = {
                    "type": "status",
                    "server_time": time.time(),
                    "manual_offset_ms": sync_state.manual_offset_ms,
                    "audio": audio_cache.status(),
                    "clients": len(clients),
                    "last_sync_report": sync_state.last_sync_report,
                }
                await _safe_send(ws, _to_json(reply), send_locks)
            elif cmd == "set_offset":
                try:
                    sync_state.manual_offset_ms = int(args.get("offset_ms", 0))
                except Exception:
                    sync_state.manual_offset_ms = 0
                await _safe_send(
                    ws,
                    _to_json(
                        {
                            "type": "ack",
                            "cmd": "set_offset",
                            "manual_offset_ms": sync_state.manual_offset_ms,
                        }
                    ),
                    send_locks,
                )
            elif cmd in {"play", "pause", "sync_now"}:
                await _safe_send(ws, _to_json({"type": "ack", "cmd": cmd}), send_locks)
            elif cmd == "report_sync":
                sync_state.last_sync_report = {
                    "video_time": args.get("video_time"),
                    "audio_pts": args.get("audio_pts"),
                    "drift": args.get("drift"),
                    "client_time": args.get("client_time"),
                    "received_at": time.time(),
                }
                if DEBUG:
                    _log(f"sync report: {sync_state.last_sync_report}")
                await _safe_send(ws, _to_json({"type": "ack", "cmd": "report_sync"}), send_locks)
    except Exception as exc:
        _log(f"WebSocket处理异常 [ID:{client_id}]: {exc}")
    finally:
        clients.discard(ws)
        send_locks.pop(ws, None)
        _log(f"WebSocket客户端断开 [ID:{client_id}]，当前连接数: {len(clients)}")


def _load_index_html(room_id: str) -> bytes:
    return (
        f"""
<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>Douyu Audio Bridge</title></head>
<body style="font-family:Arial,sans-serif;padding:24px;line-height:1.7">
<h2>Douyu Audio + Danmaku Bridge</h2>
<p>房间ID: {room_id}</p>
<p>WebSocket: ws://{WS_HOST}:{WS_PORT}</p>
<p>该服务会推送消息类型：<code>danmaku</code>、<code>audio_segment</code>、<code>audio_meta</code>，并接收 <code>control</code>。</p>
</body></html>
""".strip()
    ).encode("utf-8")


def _process_request_factory(room_id: str):
    """创建HTTP请求处理器"""
    index_bytes = _load_index_html(room_id)

    def _process_request(connection: ServerConnection, request: Request) -> Response | None:
        path = request.path

        upgrade_header = None
        for name, value in request.headers.raw_items():
            name_str = name.decode('utf-8') if isinstance(name, bytes) else str(name)
            value_str = value.decode('utf-8') if isinstance(value, bytes) else str(value)
            if name_str.lower() == "upgrade":
                upgrade_header = value_str

        if upgrade_header and upgrade_header.lower() == "websocket":
            return None

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

        if path.startswith("/healthz"):
            body = b'{"ok":true}'
            return Response(
                status_code=200,
                reason_phrase="OK",
                headers=Headers([
                    ("Content-Type", "application/json"),
                    ("Content-Length", str(len(body))),
                ]),
                body=body,
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
    parser = argparse.ArgumentParser(description="斗鱼弹幕飘过服务器")
    parser.add_argument("--room-url", default=ROOM_URL, help="斗鱼房间URL")
    parser.add_argument("--room-id", default=None, help="房间ID（可选）")
    parser.add_argument("--host", default=WS_HOST, help="绑定主机")
    parser.add_argument("--port", type=int, default=WS_PORT, help="绑定端口")
    parser.add_argument("--debug", action="store_true", help="启用调试日志")
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口（默认无头模式）")
    parser.add_argument("--max-cache", type=int, default=MAX_DANMAKU_CACHE, help="最大缓存弹幕数")
    parser.add_argument("--audio-cache-seconds", type=int, default=AUDIO_CACHE_SECONDS, help="音频缓存秒数")
    parser.add_argument("--audio-chunk-ms", type=int, default=AUDIO_CHUNK_MS, help="音频分段毫秒")
    return parser.parse_args()


async def _run_danmaku_crawler(args, queue: asyncio.Queue) -> None:
    """独立的弹幕获取任务"""
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        print(f"错误: 需要安装 Playwright: pip install playwright && playwright install chromium")
        _log(f"Playwright导入失败: {exc}")
        return

    loop = asyncio.get_running_loop()

    def on_message(line: str) -> None:
        """收到弹幕时的回调"""
        print(line)  # 总是打印弹幕
        loop.call_soon_threadsafe(
            queue.put_nowait,
            {
                "type": "danmaku",
                "text": line,
                "ts": time.time(),
            },
        )

    seg_state = {
        "next_seg_id": 1,
        "next_pts": 0.0,
        "meta_sent": False,
    }

    async def _push_audio_chunk(_: Any, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        b64_data = payload.get("b64")
        if not isinstance(b64_data, str) or not b64_data:
            return
        duration_ms = int(payload.get("duration_ms") or args.audio_chunk_ms)
        mime = str(payload.get("mime") or "audio/webm;codecs=opus")

        seg_id = seg_state["next_seg_id"]
        seg_state["next_seg_id"] += 1
        pts = float(seg_state["next_pts"])
        seg_state["next_pts"] += max(duration_ms, 100) / 1000.0

        if not seg_state["meta_sent"]:
            seg_state["meta_sent"] = True
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {
                    "type": "audio_meta",
                    "codec": "opus",
                    "mime": mime,
                    "chunk_ms": duration_ms,
                    "ts": time.time(),
                },
            )

        loop.call_soon_threadsafe(
            queue.put_nowait,
            {
                "type": "audio_segment",
                "seg_id": seg_id,
                "pts": pts,
                "duration_ms": duration_ms,
                "mime": mime,
                "data": b64_data,
                "ts": time.time(),
            },
        )

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=not args.headed)
            page = await browser.new_page()
            await page.expose_binding("__pushAudioChunk", _push_audio_chunk)

            def handle_ws(ws):
                _log(f"斗鱼WebSocket已连接: {ws.url}")
                frame_log_count = {"count": 0}
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
                        if payload_len > 0:
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
                                return
                            for line in _parse_douyu_packets(ws_buffer, binary_payload):
                                on_message(line)
                            return

                    if isinstance(payload, (bytes, bytearray)):
                        for line in _parse_douyu_packets(ws_buffer, bytes(payload)):
                            on_message(line)

                ws.on("framereceived", on_frame)
                ws.on("close", lambda: _log("斗鱼WebSocket已关闭"))

            page.on("websocket", handle_ws)
            
            _log("正在加载斗鱼页面...")
            await page.goto(args.room_url, wait_until="domcontentloaded")
            _log("✓ 斗鱼页面加载成功，开始监听弹幕")

            await page.evaluate(
                """
() => {
    if (window.__douyuAudioCaptureBooted) return;
    window.__douyuAudioCaptureBooted = true;

    const blobToBase64 = (blob) => new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onloadend = () => {
            const result = String(reader.result || '');
            const comma = result.indexOf(',');
            resolve(comma >= 0 ? result.slice(comma + 1) : result);
        };
        reader.onerror = reject;
        reader.readAsDataURL(blob);
    });

    const startCapture = async () => {
        if (window.__douyuAudioRecorder) return true;
        const video = document.querySelector('video');
        if (!video) return false;

        let stream = null;
        try {
            stream = video.captureStream ? video.captureStream() : (video.mozCaptureStream ? video.mozCaptureStream() : null);
        } catch (e) {
            return false;
        }
        if (!stream) return false;

        const tracks = stream.getAudioTracks();
        if (!tracks || tracks.length === 0) return false;

        const audioStream = new MediaStream(tracks);
        const wanted = ['audio/webm;codecs=opus', 'audio/webm'];
        let mimeType = '';
        for (const mt of wanted) {
            if (window.MediaRecorder && MediaRecorder.isTypeSupported(mt)) {
                mimeType = mt;
                break;
            }
        }

        const recorder = mimeType ? new MediaRecorder(audioStream, { mimeType }) : new MediaRecorder(audioStream);
        let stopped = false;
        let rollingTimer = null;
        let forceRestartTimer = null;
        let noChunkWatchTimer = null;
        let lastChunkAt = Date.now();

        const FORCE_RESTART_MS = 90 * 1000;
        const NO_CHUNK_TIMEOUT_MS = Math.max(%d * 4, 8000);

        const startOnce = () => {
            if (stopped) return;
            try {
                recorder.start();
                if (rollingTimer) {
                    clearTimeout(rollingTimer);
                    rollingTimer = null;
                }
                rollingTimer = setTimeout(() => {
                    try {
                        if (recorder.state === 'recording') recorder.stop();
                    } catch (e) {
                    }
                }, %d);
            } catch (e) {
            }
        };

        const hardStop = () => {
            stopped = true;
            if (rollingTimer) {
                clearTimeout(rollingTimer);
                rollingTimer = null;
            }
            if (forceRestartTimer) {
                clearTimeout(forceRestartTimer);
                forceRestartTimer = null;
            }
            if (noChunkWatchTimer) {
                clearInterval(noChunkWatchTimer);
                noChunkWatchTimer = null;
            }
            try {
                if (recorder.state === 'recording') recorder.stop();
            } catch (e) {
            }
            try {
                tracks.forEach((t) => t.stop && t.stop());
            } catch (e) {
            }
            window.__douyuAudioRecorder = null;
        };

        recorder.ondataavailable = async (ev) => {
            try {
                if (!ev.data || ev.data.size <= 0) return;
                lastChunkAt = Date.now();
                const b64 = await blobToBase64(ev.data);
                await window.__pushAudioChunk({
                    b64,
                    mime: recorder.mimeType || mimeType || 'audio/webm',
                    duration_ms: %d,
                    client_ts: Date.now()
                });
            } catch (e) {
            }
        };

        recorder.onstop = () => {
            if (!stopped) {
                setTimeout(() => startOnce(), 0);
            }
        };

        recorder.onerror = () => {
            hardStop();
        };

        tracks.forEach((t) => {
            t.onended = () => {
                hardStop();
            };
            t.onmute = () => {
            };
        });

        forceRestartTimer = setTimeout(() => {
            hardStop();
        }, FORCE_RESTART_MS);

        noChunkWatchTimer = setInterval(() => {
            if (Date.now() - lastChunkAt > NO_CHUNK_TIMEOUT_MS) {
                hardStop();
            }
        }, 2000);

        startOnce();
        window.__douyuAudioRecorder = recorder;
        return true;
    };

    setInterval(async () => {
        await startCapture();
    }, 2000);

    startCapture();
}
"""
                % (args.audio_chunk_ms, args.audio_chunk_ms, args.audio_chunk_ms)
            )
            
            # 保持浏览器运行
            await asyncio.Event().wait()
            
    except asyncio.CancelledError:
        _log("弹幕获取任务被取消")
        raise
    except Exception as e:
        print(f"错误: 弹幕获取失败: {e}")
        _log(f"弹幕获取异常: {e}")
        raise


async def _run_websocket_server(
    args,
    queue: asyncio.Queue,
    clients: Set[ServerConnection],
    send_locks: dict[ServerConnection, asyncio.Lock],
    danmaku_cache: DanmakuCache,
    audio_cache: AudioCache,
    sync_state: SyncState,
    room_id: str,
) -> None:
    """独立的WebSocket服务器任务"""
    broadcast_task = None
    
    try:
        process_request = _process_request_factory(room_id)
        
        # 创建广播任务
        broadcast_task = asyncio.create_task(
            _broadcast_loop(
                queue,
                clients,
                send_locks,
                danmaku_cache,
                audio_cache,
                sync_state,
            )
        )
        
        async with serve(
            lambda ws: _ws_handler(ws, clients, send_locks, danmaku_cache, audio_cache, sync_state),
            args.host,
            args.port,
            process_request=process_request,
        ) as server:
            print(f"✓ 弹幕飘过服务器成功启动!")
            print(f"✓ 在浏览器中打开: http://{args.host}:{args.port}")
            print(f"✓ 按 F12 打开开发者工具查看WebSocket连接状态")
            _log(f"HTTP服务器: http://{args.host}:{args.port}")
            _log(f"WebSocket地址: ws://{args.host}:{args.port}")
            print("✓ 现在会同时广播: danmaku / audio_segment / audio_meta")
            print()
            
            # 等待广播任务
            await broadcast_task
            
    except asyncio.CancelledError:
        _log("WebSocket服务器任务被取消")
        if broadcast_task:
            broadcast_task.cancel()
        raise
    except Exception as e:
        print(f"错误: WebSocket服务器失败: {e}")
        _log(f"服务器异常: {e}")
        if broadcast_task:
            broadcast_task.cancel()
        raise


async def main_async() -> None:
    args = _parse_args()
    global DEBUG
    DEBUG = args.debug

    room_id = args.room_id
    if not room_id:
        match = re.search(r"(\d+)", args.room_url)
        if not match:
            raise ValueError("ROOM_URL must contain a numeric room id.")
        room_id = match.group(1)
    
    _log(f"room_id={room_id}")

    room_info = _fetch_room_info(args.room_url)
    _log(f"房间信息 - 主播: {room_info.get('host', '')}, 标题: {room_info.get('title', '')}")

    danmaku_cache = DanmakuCache(args.max_cache)
    audio_cache = AudioCache(args.audio_cache_seconds)
    sync_state = SyncState()
    queue: asyncio.Queue = asyncio.Queue()
    clients: Set[ServerConnection] = set()
    send_locks: dict[ServerConnection, asyncio.Lock] = {}

    # 创建两个独立的任务
    server_task = asyncio.create_task(
        _run_websocket_server(
            args,
            queue,
            clients,
            send_locks,
            danmaku_cache,
            audio_cache,
            sync_state,
            room_id,
        )
    )
    crawler_task = asyncio.create_task(_run_danmaku_crawler(args, queue))

    try:
        # 并发运行两个任务，任意一个失败都会停止
        await asyncio.gather(server_task, crawler_task)
    except KeyboardInterrupt:
        print("\n收到停止信号，正在关闭...")
        server_task.cancel()
        crawler_task.cancel()
        try:
            await asyncio.gather(server_task, crawler_task, return_exceptions=True)
        except:
            pass
    except Exception as e:
        print(f"\n程序异常: {e}")
        _log(f"主任务异常: {e}")
        server_task.cancel()
        crawler_task.cancel()
        raise


if __name__ == "__main__":
    asyncio.run(main_async())
