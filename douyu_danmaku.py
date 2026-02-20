import argparse
import asyncio
import base64
import logging
import re
import struct
import sys
import urllib.request
from collections import Counter, deque
from datetime import datetime
from pathlib import Path
from typing import Set

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


async def _broadcast_loop(queue: asyncio.Queue, clients: Set[ServerConnection], cache: DanmakuCache) -> None:
    """广播弹幕到所有连接的客户端"""
    _log("广播循环已启动")
    while True:
        danmaku = await queue.get()
        cache.add(danmaku)
        
        if clients:
            _log(f"向{len(clients)}个客户端广播弹幕")
        
        dead = []
        for ws in clients:
            try:
                await ws.send(danmaku)
            except Exception as e:
                _log(f"广播失败: {e}")
                dead.append(ws)
        
        for ws in dead:
            clients.discard(ws)


async def _ws_handler(ws: ServerConnection, clients: Set[ServerConnection], cache: DanmakuCache) -> None:
    """处理WebSocket连接"""
    clients.add(ws)
    client_id = id(ws)
    _log(f"WebSocket客户端连接 [ID:{client_id}]，当前连接数: {len(clients)}")
    
    try:
        # 发送最近的弹幕给新连接的客户端
        recent = cache.get_recent(20)
        _log(f"向客户端 [ID:{client_id}] 发送最近{len(recent)}条弹幕")
        for danmaku in recent:
            try:
                await ws.send(danmaku)
            except Exception:
                break
        
        # 保持连接
        async for message in ws:
            # 客户端发来的消息（如果有）
            _log(f"收到客户端 [ID:{client_id}] 消息: {message}")
    except Exception as e:
        _log(f"WebSocket处理异常 [ID:{client_id}]: {e}")
    finally:
        clients.discard(ws)
        _log(f"WebSocket客户端断开 [ID:{client_id}]，当前连接数: {len(clients)}")


def _load_danmaku_wall_html(room_id: str) -> bytes:
    """加载弹幕墙HTML页面"""
    html_file = WEB_ROOT / "danmaku_wall.html"
    if not html_file.exists():
        # 如果文件不存在，返回简单的错误页面
        return b"<html><body><h1>danmaku_wall.html not found</h1></body></html>"
    
    template = html_file.read_text(encoding="utf-8")
    html = template.replace("{{ROOM_ID}}", room_id)
    return html.encode("utf-8")


def _process_request_factory(room_id: str):
    """创建HTTP请求处理器"""
    danmaku_wall_bytes = _load_danmaku_wall_html(room_id)

    def _process_request(connection: ServerConnection, request: Request) -> Response | None:
        path = request.path
        
        # 强制打印请求信息用于调试
        print(f"[SERVER] 收到请求: {getattr(request, 'method', 'UNKNOWN')} {path}")
        
        # 检查是否是WebSocket升级请求
        upgrade_header = None
        connection_header = None
        
        for name, value in request.headers.raw_items():
            # 统一转换为字符串
            name_str = name.decode('utf-8') if isinstance(name, bytes) else str(name)
            value_str = value.decode('utf-8') if isinstance(value, bytes) else str(value)
            
            name_lower = name_str.lower()
            
            if name_lower == "upgrade":
                upgrade_header = value_str
            if name_lower == "connection":
                connection_header = value_str
        
        print(f"[SERVER] Upgrade: {upgrade_header}, Connection: {connection_header}")
        
        # 如果是WebSocket升级请求，返回None允许升级
        if upgrade_header and upgrade_header.lower() == "websocket":
            print(f"[SERVER] ✓ WebSocket升级请求已识别，允许升级")
            return None
        
        # 否则处理普通HTTP请求
        print(f"[SERVER] 这是普通HTTP请求，返回HTML")
        if path == "/" or path.startswith("/?") or path.startswith("/index.html"):
            return Response(
                status_code=200,
                reason_phrase="OK",
                headers=Headers([
                    ("Content-Type", "text/html; charset=utf-8"),
                    ("Content-Length", str(len(danmaku_wall_bytes))),
                ]),
                body=danmaku_wall_bytes,
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
        loop.call_soon_threadsafe(queue.put_nowait, line)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=not args.headed)
            page = await browser.new_page()

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
            
            # 保持浏览器运行
            await asyncio.Event().wait()
            
    except asyncio.CancelledError:
        _log("弹幕获取任务被取消")
        raise
    except Exception as e:
        print(f"错误: 弹幕获取失败: {e}")
        _log(f"弹幕获取异常: {e}")
        raise


async def _run_websocket_server(args, queue: asyncio.Queue, clients: Set[ServerConnection], cache: DanmakuCache, room_id: str) -> None:
    """独立的WebSocket服务器任务"""
    broadcast_task = None
    
    try:
        process_request = _process_request_factory(room_id)
        
        # 创建广播任务
        broadcast_task = asyncio.create_task(_broadcast_loop(queue, clients, cache))
        
        async with serve(
            lambda ws: _ws_handler(ws, clients, cache),
            args.host,
            args.port,
            process_request=process_request,
        ) as server:
            print(f"✓ 弹幕飘过服务器成功启动!")
            print(f"✓ 在浏览器中打开: http://{args.host}:{args.port}")
            print(f"✓ 按 F12 打开开发者工具查看WebSocket连接状态")
            _log(f"HTTP服务器: http://{args.host}:{args.port}")
            _log(f"WebSocket地址: ws://{args.host}:{args.port}")
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

    # 创建弹幕缓存和客户端集合
    cache = DanmakuCache(args.max_cache)
    queue: asyncio.Queue = asyncio.Queue()
    clients: Set[ServerConnection] = set()

    # 创建两个独立的任务
    server_task = asyncio.create_task(_run_websocket_server(args, queue, clients, cache, room_id))
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
