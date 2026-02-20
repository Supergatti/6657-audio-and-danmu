(function () {
  if (window.__dyAudioBridgeInjected) return;
  window.__dyAudioBridgeInjected = true;

  const state = {
    wsUrl: 'ws://127.0.0.1:8766',
    ws: null,
    connected: false,
    audioCtx: null,
    gainNode: null,
    volume: 1.0,
    bufferSec: 1.0,
    manualOffsetMs: 0,
    autoSync: true,
    playing: true,
    audioMeta: null,
    lastAudioPts: null,
    syncBase: null,
    droppedSegments: 0,
    playedSegments: 0,
    lastDrift: 0,
    danmakuDelaySec: 0,
    decodeErrors: 0,
    wsReconnectTimer: null,
    manualClose: false,
    offsetSyncTimer: null,
    pendingOffsetMs: 0,
    dkSpeedSec: 9,
    dkFontSize: 22,
    dkOpacity: 0.95,
    dkAreaRatio: 0.45,
    dkShowId: true,
    dkShowBg: true,
    dkLessOverlap: false,
    dkStrict: false,
    dkPaused: false,
  };

  const style = document.createElement('style');
  style.textContent = `
    #dy-danmaku-overlay {
      position: fixed;
      left: 0;
      top: 0;
      width: 100vw;
      height: 45vh;
      pointer-events: none;
      overflow: hidden;
      z-index: 2147483646;
    }
    .dy-dk-item {
      position: absolute;
      right: -200%;
      white-space: nowrap;
      color: #fff;
      font-size: 22px;
      font-weight: 600;
      text-shadow: 2px 2px 4px rgba(0,0,0,.85), 0 0 6px rgba(0,0,0,.7);
      background: rgba(0,0,0,.25);
      border-radius: 14px;
      padding: 3px 10px;
      animation: dy-dk-fly linear forwards;
    }
    @keyframes dy-dk-fly {
      from { transform: translateX(0); }
      to { transform: translateX(calc(-100vw - 100%)); }
    }
    #dy-audio-panel {
      position: fixed;
      right: 16px;
      top: 90px;
      width: 320px;
      z-index: 2147483647;
      background: rgba(18,18,28,.92);
      border: 1px solid rgba(255,255,255,.15);
      border-radius: 12px;
      padding: 12px;
      color: #fff;
      font-family: 'Microsoft YaHei', sans-serif;
      font-size: 12px;
      backdrop-filter: blur(10px);
      box-shadow: 0 8px 24px rgba(0,0,0,.4);
    }
    #dy-audio-panel h3 { margin: 0 0 10px; font-size: 14px; display: flex; justify-content: space-between; }
    #dy-audio-panel .row { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
    #dy-audio-panel label { width: 70px; color: #bbb; flex-shrink: 0; }
    #dy-audio-panel input[type=text], #dy-audio-panel input[type=number] {
      flex: 1; background: rgba(255,255,255,.08); color: #fff;
      border: 1px solid rgba(255,255,255,.2); border-radius: 6px; padding: 4px 8px;
    }
    #dy-audio-panel input[type=range] { flex: 1; }
    #dy-audio-panel button {
      border: 0; border-radius: 6px; padding: 6px 10px; cursor: pointer;
      background: rgba(255,255,255,.15); color: #fff;
    }
    #dy-audio-panel button.primary { background: linear-gradient(135deg,#667eea,#764ba2); }
    #dy-audio-panel .small { font-size: 11px; color: #aab; }
    #dy-audio-panel .seg-title {
      margin: 8px 0 6px;
      color: #d2d8ff;
      font-weight: 700;
      border-top: 1px solid rgba(255,255,255,.12);
      padding-top: 8px;
    }
    #dy-audio-status-dot {
      width: 10px; height: 10px; border-radius: 50%; background: #f55; display: inline-block;
      box-shadow: 0 0 8px rgba(255,85,85,.8);
    }
    #dy-audio-status-dot.on { background: #4f4; box-shadow: 0 0 8px rgba(68,255,68,.8); }
  `;
  document.head.appendChild(style);

  const overlay = document.createElement('div');
  overlay.id = 'dy-danmaku-overlay';
  document.body.appendChild(overlay);

  const panel = document.createElement('div');
  panel.id = 'dy-audio-panel';
  panel.innerHTML = `
    <h3>
      🎧 Douyu Audio Bridge
      <span id="dy-audio-status-dot"></span>
    </h3>
    <div class="row">
      <label>WS地址</label>
      <input id="dy-ws-url" type="text" value="ws://127.0.0.1:8766" />
      <button id="dy-connect" class="primary">连接</button>
    </div>
    <div class="row">
      <label>缓冲(s)</label>
      <input id="dy-buffer" type="range" min="0.2" max="3" step="0.1" value="1" />
      <span id="dy-buffer-val">1.0</span>
    </div>
    <div class="row">
      <label>音画偏移</label>
      <input id="dy-offset" type="range" min="-5000" max="5000" step="50" value="0" />
      <span id="dy-offset-val">0ms</span>
    </div>
    <div class="small">正数：音频后移（画面慢/声音快）｜负数：音频前移（画面快/声音慢）</div>
    <div class="row">
      <button id="dy-apply-align" class="primary">应用对齐参数</button>
      <span class="small" id="dy-offset-pending">待应用: 0ms</span>
    </div>
    <div class="row">
      <label>音量</label>
      <input id="dy-volume" type="range" min="0" max="1" step="0.05" value="1" />
      <span id="dy-volume-val">100%</span>
    </div>
    <div class="row">
      <label>弹幕延时</label>
      <input id="dy-dk-delay" type="range" min="0" max="120" step="1" value="0" />
      <span id="dy-dk-delay-val">0s</span>
    </div>
    <div class="seg-title">弹幕调试</div>
    <div class="row">
      <label>弹幕速度</label>
      <input id="dy-dk-speed" type="range" min="5" max="25" step="1" value="9" />
      <span id="dy-dk-speed-val">9s</span>
    </div>
    <div class="row">
      <label>弹幕字号</label>
      <input id="dy-dk-font" type="range" min="14" max="42" step="1" value="22" />
      <span id="dy-dk-font-val">22px</span>
    </div>
    <div class="row">
      <label>弹幕透明</label>
      <input id="dy-dk-opacity" type="range" min="30" max="100" step="5" value="95" />
      <span id="dy-dk-opacity-val">95%</span>
    </div>
    <div class="row">
      <label>显示区域</label>
      <input id="dy-dk-area" type="range" min="25" max="100" step="25" value="45" />
      <span id="dy-dk-area-val">45%</span>
    </div>
    <div class="row">
      <label>显示ID</label>
      <input id="dy-dk-show-id" type="checkbox" checked />
      <label>背景</label>
      <input id="dy-dk-show-bg" type="checkbox" checked />
    </div>
    <div class="row">
      <label>减少重叠</label>
      <input id="dy-dk-less" type="checkbox" />
      <label>精简模式</label>
      <input id="dy-dk-strict" type="checkbox" />
    </div>
    <div class="row">
      <button id="dy-dk-toggle">暂停弹幕</button>
      <button id="dy-dk-clear">清空弹幕</button>
    </div>
    <div class="row">
      <button id="dy-sync-now">对齐当前帧</button>
      <button id="dy-toggle-play">暂停音频</button>
      <button id="dy-status">状态</button>
    </div>
    <div class="row">
      <label>自动校准</label>
      <input id="dy-auto-sync" type="checkbox" checked />
      <span class="small" id="dy-drift">drift: --</span>
    </div>
    <div class="small" id="dy-info">等待连接...</div>
  `;
  document.body.appendChild(panel);

  function qs(id) { return document.getElementById(id); }
  const ui = {
    wsUrl: qs('dy-ws-url'),
    connect: qs('dy-connect'),
    dot: qs('dy-audio-status-dot'),
    info: qs('dy-info'),
    buffer: qs('dy-buffer'),
    bufferVal: qs('dy-buffer-val'),
    offset: qs('dy-offset'),
    offsetVal: qs('dy-offset-val'),
    applyAlign: qs('dy-apply-align'),
    offsetPending: qs('dy-offset-pending'),
    volume: qs('dy-volume'),
    volumeVal: qs('dy-volume-val'),
    dkDelay: qs('dy-dk-delay'),
    dkDelayVal: qs('dy-dk-delay-val'),
    dkSpeed: qs('dy-dk-speed'),
    dkSpeedVal: qs('dy-dk-speed-val'),
    dkFont: qs('dy-dk-font'),
    dkFontVal: qs('dy-dk-font-val'),
    dkOpacity: qs('dy-dk-opacity'),
    dkOpacityVal: qs('dy-dk-opacity-val'),
    dkArea: qs('dy-dk-area'),
    dkAreaVal: qs('dy-dk-area-val'),
    dkShowId: qs('dy-dk-show-id'),
    dkShowBg: qs('dy-dk-show-bg'),
    dkLess: qs('dy-dk-less'),
    dkStrict: qs('dy-dk-strict'),
    dkToggle: qs('dy-dk-toggle'),
    dkClear: qs('dy-dk-clear'),
    syncNow: qs('dy-sync-now'),
    togglePlay: qs('dy-toggle-play'),
    autoSync: qs('dy-auto-sync'),
    drift: qs('dy-drift'),
    status: qs('dy-status'),
  };

  const DK_COLORS = ['#ffeb3b', '#ff9f43', '#54a0ff', '#f368e0', '#1dd1a1', '#feca57', '#ff6b6b'];
  let danmakuTracks = [];
  const danmakuQueue = [];
  const trackHeight = 40;
  function applyDanmakuArea() {
    const h = Math.floor(window.innerHeight * state.dkAreaRatio);
    overlay.style.height = `${h}px`;
    const trackCount = Math.max(1, Math.floor(h / trackHeight));
    danmakuTracks = new Array(trackCount).fill(0);
  }
  applyDanmakuArea();
  window.addEventListener('resize', applyDanmakuArea);

  function parseDanmaku(raw) {
    const m = String(raw || '').match(/\[(.*?)\]\s*(.*?):\s*(.*)/);
    if (!m) return { user: '', text: String(raw || '') };
    return { user: m[2], text: m[3] };
  }

  function findTrack() {
    const now = Date.now();
    for (let i = 0; i < danmakuTracks.length; i++) {
      if (now >= danmakuTracks[i]) return i;
    }
    return -1;
  }

  function addDanmaku(rawText) {
    const idx = findTrack();
    if (idx < 0) return;
    const dk = parseDanmaku(rawText);
    const el = document.createElement('div');
    el.className = 'dy-dk-item';
    el.textContent = (state.dkShowId && dk.user) ? `${dk.user}: ${dk.text}` : dk.text;
    el.style.top = `${idx * trackHeight + 6}px`;
    el.style.animationDuration = `${state.dkSpeedSec}s`;
    el.style.color = DK_COLORS[Math.floor(Math.random() * DK_COLORS.length)];
    el.style.fontSize = `${state.dkFontSize}px`;
    el.style.opacity = String(state.dkOpacity);
    el.style.background = state.dkShowBg ? 'rgba(0,0,0,.25)' : 'none';
    overlay.appendChild(el);

    const w = el.offsetWidth || 280;
    const tailEnterMs = (w / (window.innerWidth + w)) * state.dkSpeedSec * 1000;
    let lockMs = 200;
    if (state.dkStrict) lockMs = tailEnterMs;
    else if (state.dkLessOverlap) lockMs = tailEnterMs * 0.75;
    danmakuTracks[idx] = Date.now() + Math.max(160, lockMs);
    el.style.right = `-${w}px`;
    el.addEventListener('animationend', () => el.remove());
  }

  function enqueueDanmaku(rawText) {
    danmakuQueue.push({
      dueAt: Date.now() + Math.max(0, state.danmakuDelaySec * 1000),
      rawText,
    });
  }

  function flushDanmakuQueue() {
    if (state.dkPaused) return;
    const now = Date.now();
    let count = 0;
    while (danmakuQueue.length && danmakuQueue[0].dueAt <= now && count < 8) {
      const item = danmakuQueue.shift();
      addDanmaku(item.rawText);
      count += 1;
    }
  }

  setInterval(flushDanmakuQueue, 80);

  function getVideo() {
    return document.querySelector('video');
  }

  function getVideoTime() {
    const video = getVideo();
    return video ? video.currentTime : 0;
  }

  function ensureAudioContext() {
    if (!state.audioCtx) {
      const AudioContextCls = window.AudioContext || window.webkitAudioContext;
      state.audioCtx = new AudioContextCls();
      state.gainNode = state.audioCtx.createGain();
      state.gainNode.gain.value = state.volume;
      state.gainNode.connect(state.audioCtx.destination);
    }
    if (state.audioCtx.state === 'suspended') {
      state.audioCtx.resume().catch(() => {});
    }
  }

  function b64ToArrayBuffer(b64) {
    const binary = atob(b64);
    const len = binary.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) bytes[i] = binary.charCodeAt(i);
    return bytes.buffer;
  }

  function setConnected(ok) {
    state.connected = ok;
    if (ok) {
      ui.dot.classList.add('on');
      ui.connect.textContent = '断开';
      ui.info.textContent = '已连接，等待音频分段...';
    } else {
      ui.dot.classList.remove('on');
      ui.connect.textContent = '连接';
      ui.info.textContent = '未连接';
    }
  }

  function applyOffset(ms) {
    state.manualOffsetMs = Number(ms || 0);
    state.pendingOffsetMs = state.manualOffsetMs;
    ui.offset.value = String(state.manualOffsetMs);
    ui.offsetVal.textContent = `${state.manualOffsetMs}ms`;
    ui.offsetPending.textContent = `待应用: ${state.pendingOffsetMs}ms`;
  }

  function applyAlignNow() {
    state.manualOffsetMs = Number(state.pendingOffsetMs || 0);
    ui.offset.value = String(state.manualOffsetMs);
    ui.offsetVal.textContent = `${state.manualOffsetMs}ms`;
    ui.offsetPending.textContent = `待应用: ${state.manualOffsetMs}ms`;
    sendControl('set_offset', { offset_ms: state.manualOffsetMs });
    sendControl('sync_now', {
      video_time: getVideoTime(),
      audio_pts: state.lastAudioPts,
      client_time: Date.now(),
      offset_ms: state.manualOffsetMs,
    });
    ui.info.textContent = `已应用对齐: ${state.manualOffsetMs}ms`;
  }

  function sendControl(cmd, args = {}) {
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
    state.ws.send(JSON.stringify({ type: 'control', cmd, args }));
  }

  function updateSyncBaseFromNow() {
    if (state.lastAudioPts == null) return;
    state.syncBase = {
      videoTime: getVideoTime(),
      audioPts: state.lastAudioPts,
    };
    ui.info.textContent = `已设置对齐基准 video=${state.syncBase.videoTime.toFixed(2)} audio=${state.syncBase.audioPts.toFixed(2)}`;
    sendControl('sync_now', {
      video_time: state.syncBase.videoTime,
      audio_pts: state.syncBase.audioPts,
      client_time: Date.now(),
    });
  }

  async function handleAudioSegment(msg) {
    if (!state.playing) return;
    ensureAudioContext();

    state.lastAudioPts = Number(msg.pts || 0);

    const arr = b64ToArrayBuffer(msg.data || '');
    let audioBuffer;
    try {
      audioBuffer = await state.audioCtx.decodeAudioData(arr.slice(0));
    } catch (err) {
      state.droppedSegments += 1;
      state.decodeErrors += 1;
      if (state.decodeErrors <= 5 || state.decodeErrors % 20 === 0) {
        ui.info.textContent = `音频解码失败 seg=${msg.seg_id} dropped=${state.droppedSegments} err=${err?.name || 'decode'}`;
      }
      return;
    }

    const source = state.audioCtx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(state.gainNode);

    let delaySec = state.bufferSec;
    let drift = 0;

    if (state.syncBase) {
      const expectedVideo = state.syncBase.videoTime + (state.lastAudioPts - state.syncBase.audioPts) + state.manualOffsetMs / 1000;
      const nowVideo = getVideoTime();
      drift = expectedVideo - nowVideo;
      state.lastDrift = drift;
      ui.drift.textContent = `drift: ${drift.toFixed(3)}s`;

      if (drift < -1.8) {
        state.droppedSegments += 1;
        sendControl('report_sync', {
          video_time: nowVideo,
          audio_pts: state.lastAudioPts,
          drift,
          client_time: Date.now(),
        });
        return;
      }
      delaySec = Math.max(0, drift + state.bufferSec);

      if (state.autoSync && Math.abs(drift) > 0.8) {
        state.manualOffsetMs -= Math.round(drift * 80);
        state.pendingOffsetMs = state.manualOffsetMs;
        ui.offset.value = String(state.pendingOffsetMs);
        ui.offsetVal.textContent = `${state.pendingOffsetMs}ms`;
        ui.offsetPending.textContent = `待应用: ${state.pendingOffsetMs}ms`;
      }

      sendControl('report_sync', {
        video_time: nowVideo,
        audio_pts: state.lastAudioPts,
        drift,
        client_time: Date.now(),
      });
    }

    const when = state.audioCtx.currentTime + delaySec;
    source.start(when);
    state.playedSegments += 1;

    ui.info.textContent = `播放中 seg=${msg.seg_id} pts=${state.lastAudioPts.toFixed(2)} played=${state.playedSegments} dropped=${state.droppedSegments}`;
  }

  function connect() {
    const url = ui.wsUrl.value.trim();
    if (!url) return;
    state.wsUrl = url;
    state.manualClose = false;
    if (state.wsReconnectTimer) {
      clearTimeout(state.wsReconnectTimer);
      state.wsReconnectTimer = null;
    }

    if (state.ws) {
      try { state.ws.close(); } catch {}
      state.ws = null;
    }

    const ws = new WebSocket(state.wsUrl);
    state.ws = ws;

    ws.onopen = () => {
      setConnected(true);
      ensureAudioContext();
      sendControl('get_status');
    };

    ws.onclose = () => {
      setConnected(false);
      if (!state.manualClose) {
        ui.info.textContent = '连接断开，2秒后自动重连';
        state.wsReconnectTimer = setTimeout(() => {
          connect();
        }, 2000);
      }
    };

    ws.onerror = () => {
      ui.info.textContent = 'WebSocket 错误';
    };

    ws.onmessage = async (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }

      if (msg.type === 'hello') {
        applyOffset(Number(msg.manual_offset_ms || 0));
        ui.info.textContent = `已握手，offset=${state.manualOffsetMs}ms`;
        return;
      }

      if (msg.type === 'audio_meta') {
        state.audioMeta = msg;
        ui.info.textContent = `音频格式: ${msg.mime || msg.codec || 'unknown'}`;
        return;
      }

      if (msg.type === 'audio_segment') {
        await handleAudioSegment(msg);
        return;
      }

      if (msg.type === 'danmaku' && typeof msg.text === 'string') {
        enqueueDanmaku(msg.text);
        return;
      }

      if (msg.type === 'status') {
        ui.info.textContent = `服务端在线 clients=${msg.clients} latest_pts=${(msg.audio?.latest_pts || 0).toFixed(2)}`;
      }

      if (msg.type === 'ack' && msg.cmd === 'set_offset') {
        applyOffset(Number(msg.manual_offset_ms || state.manualOffsetMs));
        ui.info.textContent = `偏移已生效: ${state.manualOffsetMs}ms`;
      }
    };
  }

  ui.connect.addEventListener('click', () => {
    if (state.connected) {
      state.manualClose = true;
      if (state.wsReconnectTimer) {
        clearTimeout(state.wsReconnectTimer);
        state.wsReconnectTimer = null;
      }
      if (state.ws) state.ws.close();
      return;
    }
    connect();
  });

  ui.buffer.addEventListener('input', () => {
    state.bufferSec = Number(ui.buffer.value);
    ui.bufferVal.textContent = state.bufferSec.toFixed(1);
  });

  ui.offset.addEventListener('input', () => {
    state.pendingOffsetMs = Number(ui.offset.value || 0);
    ui.offsetVal.textContent = `${state.pendingOffsetMs}ms`;
    ui.offsetPending.textContent = `待应用: ${state.pendingOffsetMs}ms`;
  });

  ui.applyAlign.addEventListener('click', () => {
    applyAlignNow();
  });

  ui.volume.addEventListener('input', () => {
    state.volume = Number(ui.volume.value);
    ui.volumeVal.textContent = `${Math.round(state.volume * 100)}%`;
    if (state.gainNode) state.gainNode.gain.value = state.volume;
  });

  ui.dkDelay.addEventListener('input', () => {
    state.danmakuDelaySec = Number(ui.dkDelay.value || 0);
    ui.dkDelayVal.textContent = `${state.danmakuDelaySec}s`;
  });

  ui.dkSpeed.addEventListener('input', () => {
    state.dkSpeedSec = Number(ui.dkSpeed.value || 9);
    ui.dkSpeedVal.textContent = `${state.dkSpeedSec}s`;
  });

  ui.dkFont.addEventListener('input', () => {
    state.dkFontSize = Number(ui.dkFont.value || 22);
    ui.dkFontVal.textContent = `${state.dkFontSize}px`;
  });

  ui.dkOpacity.addEventListener('input', () => {
    const v = Number(ui.dkOpacity.value || 95);
    state.dkOpacity = Math.max(0.3, Math.min(1, v / 100));
    ui.dkOpacityVal.textContent = `${v}%`;
  });

  ui.dkArea.addEventListener('input', () => {
    const v = Number(ui.dkArea.value || 45);
    state.dkAreaRatio = Math.max(0.25, Math.min(1, v / 100));
    ui.dkAreaVal.textContent = `${v}%`;
    applyDanmakuArea();
  });

  ui.dkShowId.addEventListener('change', () => {
    state.dkShowId = !!ui.dkShowId.checked;
  });

  ui.dkShowBg.addEventListener('change', () => {
    state.dkShowBg = !!ui.dkShowBg.checked;
  });

  ui.dkLess.addEventListener('change', () => {
    state.dkLessOverlap = !!ui.dkLess.checked;
    if (state.dkLessOverlap) {
      state.dkStrict = false;
      ui.dkStrict.checked = false;
    }
  });

  ui.dkStrict.addEventListener('change', () => {
    state.dkStrict = !!ui.dkStrict.checked;
    if (state.dkStrict) {
      state.dkLessOverlap = false;
      ui.dkLess.checked = false;
    }
  });

  ui.dkToggle.addEventListener('click', () => {
    state.dkPaused = !state.dkPaused;
    ui.dkToggle.textContent = state.dkPaused ? '继续弹幕' : '暂停弹幕';
  });

  ui.dkClear.addEventListener('click', () => {
    overlay.innerHTML = '';
    danmakuQueue.length = 0;
    danmakuTracks.fill(0);
  });

  ui.syncNow.addEventListener('click', () => {
    updateSyncBaseFromNow();
  });

  ui.togglePlay.addEventListener('click', () => {
    state.playing = !state.playing;
    ui.togglePlay.textContent = state.playing ? '暂停音频' : '恢复音频';
    sendControl(state.playing ? 'play' : 'pause');
  });

  ui.autoSync.addEventListener('change', () => {
    state.autoSync = !!ui.autoSync.checked;
  });

  ui.status.addEventListener('click', () => {
    sendControl('get_status');
  });

  panel.addEventListener('click', () => {
    ensureAudioContext();
  });

  const resumeAudio = () => ensureAudioContext();
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) resumeAudio();
  });
  window.addEventListener('focus', resumeAudio);
  window.addEventListener('pageshow', resumeAudio);
  document.addEventListener('click', resumeAudio, { passive: true });
  document.addEventListener('keydown', resumeAudio, { passive: true });

  const videoObserver = new MutationObserver(() => {
    if (getVideo()) resumeAudio();
  });
  videoObserver.observe(document.documentElement, { childList: true, subtree: true });

  applyOffset(0);
  connect();
})();
