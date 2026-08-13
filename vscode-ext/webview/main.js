// Vision 控制台前端：渲染 status → 控件事件 postMessage 到扩展主线程改配置。
// 不直接 fetch（webview CSP 禁止外部网络），全部经 acquireVsCodeApi postMessage 转发。
(function () {
  'use strict';
  const vscode = acquireVsCodeApi();
  const app = document.getElementById('app');
  const toastEl = document.getElementById('toast');
  let toastTimer = null;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function section(title) {
    const s = el('div', 'section');
    s.appendChild(el('h3', 'section-title', title));
    return s;
  }

  function showToast(text) {
    toastEl.textContent = text;
    toastEl.style.display = 'block';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.style.display = 'none'; }, 3500);
  }

  function renderStatus(msg) {
    app.innerHTML = '';
    const ok = msg.ok;

    // 健康横幅
    const health = el('div', 'health ' + (ok ? 'ok' : 'down'));
    health.appendChild(el('span', 'dot', ok ? '●' : '●'));
    if (ok && msg.data) {
      const p = msg.data.proxy || {};
      health.appendChild(el('span', '', `代理运行中 v${p.version || '?'} · pid ${p.pid || '?'} · ${Math.round(p.uptime || 0)}s`));
    } else {
      health.appendChild(el('span', '', msg.error || '代理未运行'));
    }
    if (!ok && msg.startable) {
      const b = el('button', 'btn', '启动代理');
      b.onclick = () => vscode.postMessage({ type: 'startProxy' });
      health.appendChild(b);
    }
    app.appendChild(health);

    if (!ok || !msg.data) return;
    const d = msg.data;

    // 档位
    const lv = section('档位');
    const row = el('div', 'btn-row');
    [['0', 'off'], ['1', 'fast'], ['2', 'standard'], ['3', 'deep']].forEach(([v, name]) => {
      const b = el('button', 'btn' + (String(d.level) === v ? ' active' : ''), name);
      b.onclick = () => vscode.postMessage({ type: 'setLevel', level: +v });
      row.appendChild(b);
    });
    lv.appendChild(row);
    app.appendChild(lv);

    // 后端
    const be = section('后端');
    const beRow = el('div', 'btn-row');
    const mkBackend = (kind, label) => {
      const b = el('button', 'btn' + (d.backend === kind ? ' active' : ''), label);
      b.onclick = () => vscode.postMessage({ type: 'setBackend', payload: { kind } });
      return b;
    };
    beRow.appendChild(mkBackend('local', '本地'));
    beRow.appendChild(mkBackend('cloud', '云端'));
    be.appendChild(beRow);
    if (d.cloud && d.cloud.length) {
      const sel = el('select', 'select');
      d.cloud.forEach((c) => {
        const o = el('option');
        o.value = c.name;
        o.textContent = c.name + (c.has_key ? ' ✓' : ' (无key)');
        if (c.name === d.active_provider) o.selected = true;
        sel.appendChild(o);
      });
      sel.onchange = () => vscode.postMessage({ type: 'setBackend', payload: { kind: 'cloud', provider: sel.value } });
      be.appendChild(sel);
    }
    app.appendChild(be);

    // 端口
    const pt = section('端口');
    const ptIn = el('input', 'input');
    ptIn.type = 'number'; ptIn.value = d.port;
    const ptBtn = el('button', 'btn', '应用');
    ptBtn.onclick = () => vscode.postMessage({ type: 'setBackend', payload: { kind: 'local', port: +ptIn.value } });
    const ptWarn = el('div', 'warn', '⚠ 改端口需重启代理 / 会话才生效');
    pt.appendChild(ptIn); pt.appendChild(ptBtn); pt.appendChild(ptWarn);
    app.appendChild(pt);

    // 识别参数（温度 / top_p / upstream）
    const cfg = section('识别参数');
    const mkNum = (label, key, val) => {
      const wrap = el('div', 'field');
      wrap.appendChild(el('label', '', label));
      const inp = el('input', 'input');
      inp.type = 'number'; inp.step = '0.05'; inp.value = val;
      const b = el('button', 'btn', '应用');
      b.onclick = () => vscode.postMessage({ type: 'setConfig', payload: { ollama: { [key]: +inp.value } } });
      wrap.appendChild(inp); wrap.appendChild(b);
      return wrap;
    };
    cfg.appendChild(mkNum('温度', 'temperature', d.ollama && d.ollama.temperature));
    cfg.appendChild(mkNum('top_p', 'top_p', d.ollama && d.ollama.top_p));
    const upWrap = el('div', 'field');
    upWrap.appendChild(el('label', '', '上游'));
    const upIn = el('input', 'input');
    upIn.type = 'text'; upIn.value = d.upstream || '';
    const upBtn = el('button', 'btn', '应用');
    upBtn.onclick = () => vscode.postMessage({ type: 'setConfig', payload: { upstream: upIn.value } });
    upWrap.appendChild(upIn); upWrap.appendChild(upBtn);
    cfg.appendChild(upWrap);
    app.appendChild(cfg);

    // 状态汇总
    const sv = section('状态');
    sv.appendChild(el('div', 'kv', 'ollama: ' + (d.ollama_service && d.ollama_service.running
      ? '运行中 ' + (d.ollama_service.model || '')
      : '未运行')));
    (d.cloud || []).forEach((c) => {
      sv.appendChild(el('div', 'kv', `云端 ${c.name}: ${c.model || '(未配model)'} key=${c.has_key ? '✓' : '✗'}`));
    });
    app.appendChild(sv);
  }

  window.addEventListener('message', (e) => {
    const m = e.data;
    if (m.type === 'status') renderStatus(m);
    else if (m.type === 'notice') showToast(m.text || '');
    else if (m.type === 'result' && !m.ok) showToast('操作失败: ' + (m.error || '未知错误'));
  });

  // 打开即刷新 + 每 5s 轮询（轻量，仅读状态）
  vscode.postMessage({ type: 'refresh' });
  setInterval(() => vscode.postMessage({ type: 'refresh' }), 5000);
})();
