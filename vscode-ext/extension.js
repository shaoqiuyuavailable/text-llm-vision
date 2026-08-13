// text-llm-vision 可视化插件主线程。
// 职责：侧边栏 Webview View + 把 webview 的 postMessage 转发成对代理 /api/* 的 HTTP 调用。
// 复用约定：所有配置读写都走 proxy 的控制 API（内部复用 toggle/config_loader），本文件不碰任何后端逻辑。
'use strict';

const vscode = require('vscode');
const { exec } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const DEPLOY_DIR = () => path.join(os.homedir(), '.claude', 'vision-eyes');

// 读控制 API 地址：vision.proxyUrl 显式覆盖 > vision.port > 部署目录 config.json 的 port
function readConfig() {
  const cfg = vscode.workspace.getConfiguration('vision');
  let port = cfg.get('port') || 8787;
  let baseUrl = (cfg.get('proxyUrl') || '').trim();
  if (!baseUrl) {
    try {
      const raw = JSON.parse(fs.readFileSync(path.join(DEPLOY_DIR(), 'config.json'), 'utf8'));
      if (raw.port) port = raw.port;
    } catch (e) { /* 用默认端口 */ }
    baseUrl = `http://localhost:${port}`;
  }
  return { port, baseUrl };
}

// 调代理控制 API（带 8s 超时）。失败不抛，返回 {ok,status,data}
async function api(baseUrl, method, p, body) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 8000);
  try {
    const resp = await fetch(`${baseUrl}${p}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
    const data = await resp.json().catch(() => ({}));
    return { ok: resp.ok, status: resp.status, data };
  } catch (e) {
    return { ok: false, status: 0, data: { error: e.message || String(e) } };
  } finally {
    clearTimeout(timer);
  }
}

class VisionViewProvider {
  constructor(context) {
    this.context = context;
    this.view = null;
  }

  resolveWebviewView(webviewView) {
    this.view = webviewView;
    webviewView.webview.options = { enableScripts: true };
    webviewView.webview.html = this._html(webviewView.webview);
    webviewView.webview.onDidReceiveMessage((msg) => this._onMessage(msg));
    this._refresh();
  }

  async _onMessage(msg) {
    switch (msg.type) {
      case 'refresh': await this._refresh(); break;
      case 'setLevel': await this._post('/api/level', { level: msg.level }); break;
      case 'setBackend': await this._post('/api/backend', msg.payload); break;
      case 'setConfig': await this._post('/api/config', { patch: msg.payload }); break;
      case 'startProxy': await this._startProxy(); break;
    }
  }

  async _post(p, body) {
    const { baseUrl } = readConfig();
    const res = await api(baseUrl, 'POST', p, body);
    if (res.ok) {
      await this._send({ type: 'result', ok: true });
      if (res.data.port_changed_requires_restart) {
        await this._send({ type: 'notice', text: '端口已改：需重启代理 / 会话才生效' });
      }
      await this._refresh();
    } else {
      await this._send({ type: 'result', ok: false, error: res.data.error || `HTTP ${res.status}` });
    }
  }

  async _refresh() {
    const { baseUrl } = readConfig();
    const res = await api(baseUrl, 'GET', '/api/status');
    const startable = fs.existsSync(path.join(DEPLOY_DIR(), 'start_proxy.py'));
    await this._send({
      type: 'status',
      ok: res.ok,
      error: res.ok ? null : (res.data.error || '代理未运行'),
      data: res.ok ? res.data : null,
      startable,
    });
  }

  _startProxy() {
    const py = path.join(DEPLOY_DIR(), 'start_proxy.py');
    if (!fs.existsSync(py)) {
      return this._send({ type: 'notice', text: '未找到 start_proxy.py（先运行 install.py 部署）' });
    }
    const pythonCmd = process.platform === 'win32' ? 'python' : 'python3';
    exec(`"${pythonCmd}" "${py}"`, { cwd: path.dirname(py), windowsHide: true, timeout: 30000 }, async (err, stdout) => {
      await this._send({ type: 'notice', text: (stdout || '').trim() || (err ? (err.message || String(err)) : '代理已启动') });
      await this._refresh();
    });
    return Promise.resolve();
  }

  async _send(msg) {
    if (this.view) await this.view.webview.postMessage(msg);
  }

  _html(webview) {
    const getUri = (p) => webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, 'webview', p));
    const csp = webview.cspSource;
    return `<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${csp}; script-src ${csp};">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="${getUri('style.css')}">
<title>Vision 控制台</title>
</head><body>
<div id="toast" class="toast" style="display:none"></div>
<div id="app"></div>
<script src="${getUri('main.js')}"></script>
</body></html>`;
  }
}

function activate(context) {
  const provider = new VisionViewProvider(context);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider('vision.view', provider,
      { webviewOptions: { retainContextWhenHidden: true } }),
    vscode.commands.registerCommand('vision.refresh', () => provider._refresh()),
    vscode.commands.registerCommand('vision.startProxy', () => provider._startProxy())
  );
}
exports.activate = activate;

function deactivate() {}
exports.deactivate = deactivate;
