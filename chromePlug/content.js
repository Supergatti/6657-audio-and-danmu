/**
 * 斗鱼弹幕叠加 - YouTube直播弹幕注入脚本
 * 连接本地弹幕服务(ws://127.0.0.1:8766)，在YouTube页面叠加弹幕
 *
 * 功能：
 *  - 是否显示用户ID
 *  - 减少重叠（加大轨道间隔）
 *  - 精简无重叠模式（无空闲轨道直接丢弃）
 *  - 显示区域：全屏 / 半屏 / 四分之一屏
 */

(function () {
  if (document.getElementById('danmaku-ext-overlay')) return;

  // ── 样式 ──────────────────────────────────────────────────────────────
  const style = document.createElement('style');
  style.textContent = `
    #danmaku-ext-overlay {
      position: fixed;
      top: 0; left: 0;
      width: 100vw;
      pointer-events: none;
      z-index: 2147483647;
      overflow: hidden;
      transition: height 0.3s;
    }
    .dk-item {
      position: absolute;
      white-space: nowrap;
      font-weight: 600;
      text-shadow: 2px 2px 4px rgba(0,0,0,0.9), 0 0 8px rgba(0,0,0,0.7);
      padding: 4px 12px;
      border-radius: 16px;
      background: rgba(0,0,0,0.25);
      animation: dk-fly linear forwards;
      will-change: transform;
    }
    @keyframes dk-fly {
      from { transform: translateX(0); }
      to   { transform: translateX(calc(-100vw - 100%)); }
    }
    #danmaku-ext-panel {
      position: fixed;
      top: 80px; left: 50%;
      transform: translateX(-50%);
      background: rgba(12, 12, 22, 0.92);
      backdrop-filter: blur(14px);
      border: 1px solid rgba(255,255,255,0.13);
      padding: 14px 16px 12px;
      border-radius: 14px;
      z-index: 2147483647;
      pointer-events: auto;
      color: #fff;
      font-family: 'Microsoft YaHei', sans-serif;
      font-size: 13px;
      width: 320px;
      box-shadow: 0 8px 32px rgba(0,0,0,0.55);
      cursor: move;
      user-select: none;
    }
    #danmaku-ext-panel h3 {
      margin: 0 0 10px;
      font-size: 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .dk-status {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 10px;
    }
    #dk-dot {
      width: 10px; height: 10px;
      border-radius: 50%;
      background: #f44;
      box-shadow: 0 0 8px rgba(255,68,68,0.7);
      animation: dk-pulse 2s infinite;
      flex-shrink: 0;
    }
    #dk-dot.on { background: #4f4; box-shadow: 0 0 8px rgba(68,255,68,0.7); }
    @keyframes dk-pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
    #danmaku-ext-panel .dk-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 8px;
    }
    #danmaku-ext-panel label.dk-lbl {
      color: #aaa;
      min-width: 64px;
      font-size: 12px;
      flex-shrink: 0;
    }
    #danmaku-ext-panel input[type=range] {
      flex: 1;
      height: 4px;
      accent-color: #5f8fff;
      cursor: pointer;
    }
    #danmaku-ext-panel span.val {
      min-width: 36px;
      text-align: right;
      font-size: 12px;
      font-family: monospace;
      color: #ddd;
    }
    .dk-sep {
      height: 1px;
      background: rgba(255,255,255,0.1);
      margin: 10px 0;
    }
    .dk-toggle-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 8px;
    }
    .dk-toggle-row > span {
      font-size: 12px;
      color: #ccc;
    }
    .dk-sw {
      position: relative;
      width: 36px; height: 20px;
      flex-shrink: 0;
    }
    .dk-sw input { opacity: 0; width: 0; height: 0; }
    .dk-sw-track {
      position: absolute;
      inset: 0;
      border-radius: 10px;
      background: rgba(255,255,255,0.15);
      transition: background 0.2s;
      cursor: pointer;
    }
    .dk-sw-track::after {
      content: '';
      position: absolute;
      left: 3px; top: 3px;
      width: 14px; height: 14px;
      border-radius: 50%;
      background: #fff;
      transition: transform 0.2s;
    }
    .dk-sw input:checked + .dk-sw-track { background: #5f8fff; }
    .dk-sw input:checked + .dk-sw-track::after { transform: translateX(16px); }
    .dk-area-btns {
      display: flex;
      gap: 6px;
      flex: 1;
    }
    .dk-area-btn {
      flex: 1;
      padding: 5px 4px;
      border: 1px solid rgba(255,255,255,0.18);
      border-radius: 7px;
      background: rgba(255,255,255,0.06);
      color: #bbb;
      font-size: 11px;
      cursor: pointer;
      font-family: 'Microsoft YaHei', sans-serif;
      text-align: center;
      transition: all 0.2s;
    }
    .dk-area-btn:hover { background: rgba(255,255,255,0.14); color: #fff; }
    .dk-area-btn.active {
      background: linear-gradient(135deg,#667eea,#764ba2);
      border-color: transparent;
      color: #fff;
    }
    .dk-btns {
      display: flex;
      gap: 8px;
      margin-top: 10px;
    }
    #danmaku-ext-panel .dk-btns button {
      flex: 1;
      padding: 7px;
      border: none;
      border-radius: 8px;
      cursor: pointer;
      font-size: 12px;
      font-family: 'Microsoft YaHei', sans-serif;
      background: rgba(255,255,255,0.1);
      color: #fff;
      transition: background 0.2s;
    }
    #danmaku-ext-panel .dk-btns button:hover { background: rgba(255,255,255,0.2); }
    #dk-toggle-btn { background: linear-gradient(135deg,#667eea,#764ba2) !important; }
    #dk-ws-input {
      flex: 1;
      background: rgba(255,255,255,0.08);
      border: 1px solid rgba(255,255,255,0.15);
      border-radius: 6px;
      padding: 4px 8px;
      color: #fff;
      font-size: 11px;
    }
    #dk-reconnect {
      flex: 0 0 auto;
      padding: 4px 10px;
      border: none;
      border-radius: 6px;
      background: rgba(255,255,255,0.12);
      color: #fff;
      font-size: 11px;
      cursor: pointer;
    }
    #dk-reconnect:hover { background: rgba(255,255,255,0.22); }
    #dk-hide-panel {
      background: none !important;
      border: 1px solid rgba(255,255,255,0.2) !important;
      border-radius: 6px !important;
      color: #aaa !important;
      font-size: 14px !important;
      padding: 1px 8px !important;
      line-height: 1;
      cursor: pointer;
    }
    #dk-show-fab {
      position: fixed;
      bottom: 80px; right: 20px;
      width: 44px; height: 44px;
      border-radius: 50%;
      background: rgba(12,12,22,0.88);
      border: 1px solid rgba(255,255,255,0.2);
      color: #fff;
      font-size: 20px;
      cursor: pointer;
      pointer-events: auto;
      z-index: 2147483647;
      display: none;
      align-items: center;
      justify-content: center;
      box-shadow: 0 4px 16px rgba(0,0,0,0.4);
    }
    #dk-show-fab:hover { background: rgba(40,40,60,0.95); }
  `;
  document.head.appendChild(style);

  // ── 弹幕层 ────────────────────────────────────────────────────────────
  const overlay = document.createElement('div');
  overlay.id = 'danmaku-ext-overlay';
  document.body.appendChild(overlay);

  // ── 控制面板 ──────────────────────────────────────────────────────────
  const panel = document.createElement('div');
  panel.id = 'danmaku-ext-panel';
  panel.innerHTML = `
    <h3>
      🎉 斗鱼弹幕叠加
      <button id="dk-hide-panel" title="最小化">−</button>
    </h3>
    <div class="dk-status">
      <div id="dk-dot"></div>
      <span id="dk-status-text">连接中...</span>
    </div>
    <div class="dk-row">
      <label class="dk-lbl">服务器</label>
      <input id="dk-ws-input" type="text" value="ws://127.0.0.1:8766">
      <button id="dk-reconnect">重连</button>
    </div>
    <div class="dk-sep"></div>
    <div class="dk-row">
      <label class="dk-lbl">速度</label>
      <input type="range" id="dk-speed" min="5" max="25" value="10" step="1">
      <span class="val" id="dk-speed-val">10s</span>
    </div>
    <div class="dk-row">
      <label class="dk-lbl">字号</label>
      <input type="range" id="dk-fontsize" min="14" max="40" value="22" step="2">
      <span class="val" id="dk-fontsize-val">22px</span>
    </div>
    <div class="dk-row">
      <label class="dk-lbl">透明度</label>
      <input type="range" id="dk-opacity" min="30" max="100" value="90" step="5">
      <span class="val" id="dk-opacity-val">90%</span>
    </div>
    <div class="dk-row">
      <label class="dk-lbl">延时</label>
      <input type="range" id="dk-delay" min="0" max="120" value="0" step="1">
      <span class="val" id="dk-delay-val">0s</span>
    </div>
    <div class="dk-sep"></div>
    <div class="dk-row">
      <label class="dk-lbl">显示区域</label>
      <div class="dk-area-btns">
        <div class="dk-area-btn" data-area="quarter">¼ 屏</div>
        <div class="dk-area-btn active" data-area="half">½ 屏</div>
        <div class="dk-area-btn" data-area="full">全屏</div>
      </div>
    </div>
    <div class="dk-sep"></div>
    <div class="dk-toggle-row">
      <span>显示用户ID</span>
      <label class="dk-sw">
        <input type="checkbox" id="dk-show-id" checked>
        <span class="dk-sw-track"></span>
      </label>
    </div>
    <div class="dk-toggle-row">
      <span>弹幕背景</span>
      <label class="dk-sw">
        <input type="checkbox" id="dk-show-bg" checked>
        <span class="dk-sw-track"></span>
      </label>
    </div>
    <div class="dk-toggle-row">
      <span>减少重叠</span>
      <label class="dk-sw">
        <input type="checkbox" id="dk-less-overlap">
        <span class="dk-sw-track"></span>
      </label>
    </div>
    <div class="dk-toggle-row">
      <span>精简模式</span>
      <label class="dk-sw">
        <input type="checkbox" id="dk-strict-mode">
        <span class="dk-sw-track"></span>
      </label>
    </div>
    <div class="dk-btns">
      <button id="dk-toggle-btn">暂停弹幕</button>
      <button id="dk-clear-btn">清空屏幕</button>
    </div>
  `;
  document.body.appendChild(panel);

  // 浮动按钮（面板隐藏后显示）
  const fab = document.createElement('div');
  fab.id = 'dk-show-fab';
  fab.innerHTML = '💬';
  fab.title = '显示弹幕面板';
  fab.style.display = 'none';
  document.body.appendChild(fab);

  // ── 面板拖拽 ──────────────────────────────────────────────────────────
  let dragOffset = null;
  panel.addEventListener('mousedown', (e) => {
    if (['INPUT','BUTTON','LABEL','SPAN'].includes(e.target.tagName)) return;
    const rect = panel.getBoundingClientRect();
    dragOffset = { x: e.clientX - rect.left, y: e.clientY - rect.top };
  });
  document.addEventListener('mousemove', (e) => {
    if (!dragOffset) return;
    panel.style.transform = 'none';
    panel.style.left = `${e.clientX - dragOffset.x}px`;
    panel.style.top =  `${e.clientY - dragOffset.y}px`;
  });
  document.addEventListener('mouseup', () => { dragOffset = null; });

  // ── 弹幕引擎 ──────────────────────────────────────────────────────────
  const COLORS = [
    '#FF6B9D','#FFC312','#12CBC4','#FDA7DF','#5F27CD',
    '#00D2D3','#FF9F43','#54a0ff','#ff6b6b','#48dbfb',
    '#1dd1a1','#ffeaa7','#a29bfe','#fd79a8','#e17055'
  ];

  /** 运行时配置 */
  const cfg = {
    speed: 10,
    fontSize: 22,
    opacity: 0.9,
    delaySec: 0,
    trackHeight: 52,    // 每轨道高度 px
    areaRatio: 0.5,     // 显示区域占屏幕比例：0.25 / 0.5 / 1.0
    showId: true,       // 是否显示用户 ID
    showBg: true,       // 是否显示弹幕背景
    lessOverlap: false, // 减少重叠模式
    strictMode: false,  // 精简无重叠模式
  };

  /** 轨道占用时间戳 */
  let tracks = [];

  /** 根据 areaRatio 重新计算轨道数和 overlay 高度 */
  function applyAreaRatio() {
    const h = Math.floor(window.innerHeight * cfg.areaRatio);
    overlay.style.height = `${h}px`;
    const count = Math.max(1, Math.floor(h / cfg.trackHeight));
    tracks = new Array(count).fill(0);
  }
  applyAreaRatio();
  window.addEventListener('resize', applyAreaRatio);

  let paused = false;
  const delayedQueue = [];

  function enqueueDanmaku(dk) {
    const delayMs = Math.max(0, cfg.delaySec * 1000);
    delayedQueue.push({ dueAt: Date.now() + delayMs, dk });
  }

  function flushDelayedQueue() {
    if (paused) return;
    const now = Date.now();
    let n = 0;
    while (delayedQueue.length && delayedQueue[0].dueAt <= now && n < 8) {
      const item = delayedQueue.shift();
      addDanmaku(item.dk);
      n++;
    }
  }

  setInterval(flushDelayedQueue, 80);

  /**
   * tracks[i] 存"轨道最早可复用的时间戳"（Date.now() 绝对值）
   * 初始为 0，表示立刻可用
   * findTrack 只需判断 now >= tracks[i]
   */
  function findTrack() {
    const now = Date.now();
    for (let i = 0; i < tracks.length; i++) {
      if (now >= tracks[i]) return i;
    }
    return -1;
  }

  /**
   * 计算轨道锁定时长（ms），基于前一条弹幕的实际宽度 w。
   * 弹幕【尾部进入视口】时刻：t = w / (vw + w) × speed × 1000 ms
   * 精简模式：恰好等于 t，前条尾部刚进入视口后条才出发，零重叠
   * 减少重叠：0.75 × t，轻微尾部重叠，密度适中
   * 普通模式：固定 200ms，密集飘动，允许重叠
   */
  function calcTrackFreeMs(w) {
    const vw = window.innerWidth;
    const tailEnterMs = (w / (vw + w)) * cfg.speed * 1000;
    if (cfg.strictMode)  return tailEnterMs;
    if (cfg.lessOverlap) return tailEnterMs * 0.75;
    return 200;
  }

  function addDanmaku({ user, text }) {
    const idx = findTrack();
    if (idx === -1) return;
    const el = document.createElement('div');
    el.className = 'dk-item';
    el.textContent = (cfg.showId && user) ? `${user}: ${text}` : text;
    el.style.fontSize          = `${cfg.fontSize}px`;
    el.style.opacity           = cfg.opacity;
    el.style.color             = COLORS[Math.floor(Math.random() * COLORS.length)];
    el.style.background        = cfg.showBg ? 'rgba(0,0,0,0.25)' : 'none';
    el.style.top               = `${idx * cfg.trackHeight + 8}px`;
    el.style.right             = '-200%';
    el.style.animationDuration = `${cfg.speed}s`;
    overlay.appendChild(el);

    const w = el.offsetWidth || 300;
    el.style.right = `-${w}px`;

    // 轨道锁定到"该弹幕尾部离开右边缘"之后，下一条才能上同一行
    tracks[idx] = Date.now() + calcTrackFreeMs(w);

    el.addEventListener('animationend', () => el.remove());
  }

  function parseDanmaku(msg) {
    const m = msg.match(/\[(.*?)\]\s*(.*?):\s*(.*)/);
    if (m) return { time: m[1], user: m[2], text: m[3] };
    return { time: '', user: '', text: msg };
  }

  // ── WebSocket ─────────────────────────────────────────────────────────
  let ws = null;
  let wsReconnectTimer = null;

  function setStatus(connected, text) {
    document.getElementById('dk-dot').className = connected ? 'on' : '';
    document.getElementById('dk-status-text').textContent = text;
  }

  function connectWS() {
    if (ws) { ws.onclose = null; ws.close(); }
    clearTimeout(wsReconnectTimer);
    const url = document.getElementById('dk-ws-input').value.trim() || 'ws://127.0.0.1:8766';
    setStatus(false, '连接中...');
    try {
      ws = new WebSocket(url);
      ws.onopen = () => setStatus(true, '已连接');
      ws.onmessage = (e) => {
        let raw = e.data;
        if (typeof raw !== 'string') return;
        try {
          const obj = JSON.parse(raw);
          if (obj && obj.type === 'danmaku' && typeof obj.text === 'string') {
            raw = obj.text;
          } else {
            return;
          }
        } catch {
          // 兼容旧版纯文本弹幕消息
        }
        const dk = parseDanmaku(raw);
        enqueueDanmaku(dk);
      };
      ws.onerror = () => setStatus(false, '连接错误');
      ws.onclose = () => {
        setStatus(false, '断开 · 5秒后重连');
        wsReconnectTimer = setTimeout(connectWS, 5000);
      };
    } catch {
      setStatus(false, '连接失败');
      wsReconnectTimer = setTimeout(connectWS, 5000);
    }
  }

  // ── 控件绑定 ──────────────────────────────────────────────────────────
  function bindSlider(id, valId, unit, key, scale) {
    const slider = document.getElementById(id);
    const valEl  = document.getElementById(valId);
    slider.addEventListener('input', () => {
      const v = parseFloat(slider.value);
      cfg[key] = scale ? v / scale : v;
      valEl.textContent = `${slider.value}${unit}`;
    });
  }
  bindSlider('dk-speed',    'dk-speed-val',    's',  'speed',    null);
  bindSlider('dk-fontsize', 'dk-fontsize-val', 'px', 'fontSize', null);
  bindSlider('dk-opacity',  'dk-opacity-val',  '%',  'opacity',  100);
  bindSlider('dk-delay',    'dk-delay-val',    's',  'delaySec', null);

  // 显示区域按钮组
  const AREA_MAP = { quarter: 0.25, half: 0.5, full: 1.0 };
  document.querySelectorAll('.dk-area-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.dk-area-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      cfg.areaRatio = AREA_MAP[btn.dataset.area];
      applyAreaRatio();
    });
  });

  // 开关：显示用户ID
  document.getElementById('dk-show-id').addEventListener('change', function () {
    cfg.showId = this.checked;
  });

  // 开关：弹幕背景
  document.getElementById('dk-show-bg').addEventListener('change', function () {
    cfg.showBg = this.checked;
  });

  // 开关：减少重叠（与精简模式互斥）
  document.getElementById('dk-less-overlap').addEventListener('change', function () {
    cfg.lessOverlap = this.checked;
    if (this.checked) {
      document.getElementById('dk-strict-mode').checked = false;
      cfg.strictMode = false;
    }
  });

  // 开关：精简无重叠模式（与减少重叠互斥）
  document.getElementById('dk-strict-mode').addEventListener('change', function () {
    cfg.strictMode = this.checked;
    if (this.checked) {
      document.getElementById('dk-less-overlap').checked = false;
      cfg.lessOverlap = false;
    }
  });

  document.getElementById('dk-toggle-btn').addEventListener('click', function () {
    paused = !paused;
    this.textContent = paused ? '继续弹幕' : '暂停弹幕';
    if (!paused) flushDelayedQueue();
  });

  document.getElementById('dk-clear-btn').addEventListener('click', () => {
    overlay.innerHTML = '';
    tracks.fill(0);
    delayedQueue.length = 0;
  });

  document.getElementById('dk-reconnect').addEventListener('click', connectWS);

  document.getElementById('dk-hide-panel').addEventListener('click', () => {
    panel.style.display = 'none';
    fab.style.display = 'flex';
  });
  fab.addEventListener('click', () => {
    panel.style.display = '';
    fab.style.display = 'none';
  });

  // ── 启动 ──────────────────────────────────────────────────────────────
  connectWS();
})();
