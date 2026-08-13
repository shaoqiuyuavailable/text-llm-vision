// text-llm-vision 可视化插件（TreeView 版）。
// WebviewView 在当前环境（Claude Code for VS Code）resolve 不触发，改用 TreeView：
// 侧边栏树节点展示状态，点击节点弹 QuickPick/输入框修改配置，经代理 /api/* 生效。
'use strict';

const vscode = require('vscode');
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const DEPLOY_DIR = () => path.join(os.homedir(), '.claude', 'vision-eyes');
const LEVEL_NAMES = { 0: 'off', 1: 'fast', 2: 'standard', 3: 'deep' };

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

async function api(baseUrl, method, p, body) {
  // 全程 try/catch：任何异常都不外抛（防未捕获 rejection 崩扩展宿主→循环重启弹窗）
  let ctrl = null;
  let timer = null;
  try {
    ctrl = new AbortController();
    timer = setTimeout(() => { try { ctrl.abort(); } catch (e) {} }, 8000);
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
    if (timer) clearTimeout(timer);
  }
}

class VisionTreeItem extends vscode.TreeItem {
  constructor(label, contextValue, description, action) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.contextValue = contextValue || 'info';
    this.description = description || '';
    if (action) {
      this.command = { command: 'vision.action', title: '操作', arguments: [action] };
    }
  }
}

class VisionTreeProvider {
  constructor() {
    this._onDidChangeTreeData = new vscode.EventEmitter();
    this.onDidChangeTreeData = this._onDidChangeTreeData.event;
    this.status = null;
  }
  refresh() { this._onDidChangeTreeData.fire(); }

  async getChildren() {
    const items = [];
    let res;
    try {
      const { baseUrl } = readConfig();
      res = await api(baseUrl, 'GET', '/api/status');
    } catch (e) {
      items.push(new VisionTreeItem('⚠ 读取状态异常', 'info', (e && e.message) || String(e)));
      return items;
    }
    if (!res.ok) {
      items.push(new VisionTreeItem('⚠ 代理未运行', 'proxyDown', (res.data && res.data.error) || ''));
      items.push(new VisionTreeItem('▶ 启动代理', 'startProxy', '', { kind: 'startProxy' }));
      return items;
    }
    const d = res.data;
    this.status = d;
    items.push(new VisionTreeItem(`档位: ${LEVEL_NAMES[d.level] || d.level} (${d.level})`, 'level', '点击切换', { kind: 'level' }));
    items.push(new VisionTreeItem(`后端: ${d.backend}${d.active_provider ? ' (' + d.active_provider + ')' : ''}`, 'backend', '点击切换', { kind: 'backend' }));
    items.push(new VisionTreeItem(`端口: ${d.port}`, 'port', '点击修改', { kind: 'port' }));
    items.push(new VisionTreeItem(`温度: ${d.ollama && d.ollama.temperature !== undefined ? d.ollama.temperature : '-'}`, 'temp', '点击修改', { kind: 'temp' }));
    items.push(new VisionTreeItem(`top_p: ${d.ollama && d.ollama.top_p !== undefined ? d.ollama.top_p : '-'}`, 'topp', '点击修改', { kind: 'topp' }));
    items.push(new VisionTreeItem(`上游: ${d.upstream || '-'}`, 'upstream', '点击修改', { kind: 'upstream' }));
    items.push(new VisionTreeItem('── 云端厂商 ──', 'header', ''));
    (d.cloud || []).forEach((c) => {
      items.push(new VisionTreeItem(`${c.name}: ${c.model || '(未配model)'} key=${c.has_key ? '✓' : '✗'}`,
        'provider', c.name === d.active_provider ? '当前' : '点击切换', { kind: 'provider', provider: c.name }));
    });
    items.push(new VisionTreeItem(`代理: ${d.proxy && d.proxy.status === 'ok' ? '运行中' : '?'} v${(d.proxy && d.proxy.version) || '?'} · pid ${(d.proxy && d.proxy.pid) || '?'}`, 'info', ''));
    items.push(new VisionTreeItem(`ollama: ${d.ollama_service && d.ollama_service.running ? '运行中 ' + (d.ollama_service.model || '') : '未运行'}`, 'info', ''));
    return items;
  }

  getTreeItem(element) { return element; }
}

async function post(baseUrl, p, body) {
  const res = await api(baseUrl, 'POST', p, body);
  if (!res.ok) {
    vscode.window.showErrorMessage('请求失败: ' + ((res.data && res.data.error) || `HTTP ${res.status}`));
    return null;
  }
  return res.data;
}

function startProxy() {
  return new Promise((resolve) => {
    const py = path.join(DEPLOY_DIR(), 'start_proxy.py');
    if (!fs.existsSync(py)) {
      vscode.window.showErrorMessage('未找到 start_proxy.py（先运行 install.py 部署）');
      return resolve();
    }
    const pythonCmd = process.platform === 'win32' ? 'python' : 'python3';
    // spawn + windowsHide + detached + stdio ignore：Windows 上不弹 cmd 黑窗的标准做法
    const child = spawn(pythonCmd, [py], {
      cwd: path.dirname(py),
      windowsHide: true,
      detached: true,
      stdio: 'ignore',
    });
    child.on('error', (err) => {
      vscode.window.showErrorMessage('启动代理失败: ' + err.message);
      resolve();
    });
    child.on('exit', (code) => {
      vscode.window.showInformationMessage(code === 0 ? '代理已启动（或已在运行）' : `启动代理结束，退出码 ${code}`);
      resolve();
    });
    child.unref();
  });
}

function activate(context) {
  const provider = new VisionTreeProvider();
  try {
    const treeView = vscode.window.createTreeView('vision.view', { treeDataProvider: provider, showCollapseAll: false });
    context.subscriptions.push(treeView);
  } catch (e) {
    vscode.window.showErrorMessage('Vision TreeView 创建失败: ' + ((e && e.message) || e));
  }
  context.subscriptions.push(
    vscode.commands.registerCommand('vision.action', async (action) => {
      if (!action) return;
      const { baseUrl } = readConfig();
      try {
        switch (action.kind) {
          case 'startProxy':
            await startProxy();
            break;
          case 'level': {
            const pick = await vscode.window.showQuickPick(
              [{ label: 'off (0)', value: 0 }, { label: 'fast (1)', value: 1 },
               { label: 'standard (2)', value: 2 }, { label: 'deep (3)', value: 3 }],
              { placeHolder: '选择档位' });
            if (pick) await post(baseUrl, '/api/level', { level: pick.value });
            break;
          }
          case 'backend': {
            const pick = await vscode.window.showQuickPick(
              [{ label: '本地 (local)', value: 'local' }, { label: '云端 (cloud)', value: 'cloud' }],
              { placeHolder: '选择后端' });
            if (!pick) break;
            if (pick.value === 'cloud') {
              if (provider.status && provider.status.cloud && provider.status.cloud.length) {
                const prov = await vscode.window.showQuickPick(
                  provider.status.cloud.map((c) => ({ label: c.name + (c.has_key ? ' ✓' : ' (无key)'), value: c.name })),
                  { placeHolder: '选择云端厂商' });
                if (prov) await post(baseUrl, '/api/backend', { kind: 'cloud', provider: prov.value });
              } else {
                vscode.window.showErrorMessage('未配置任何云端厂商（编辑 config.json cloud.clouds）');
              }
            } else {
              await post(baseUrl, '/api/backend', { kind: 'local' });
            }
            break;
          }
          case 'port': {
            const cur = provider.status ? provider.status.port : 8787;
            const val = await vscode.window.showInputBox({
              value: String(cur), prompt: '输入新端口（需重启代理/会话才生效）',
              validateInput: (v) => /^\d{1,5}$/.test(v) ? null : '端口必须是数字',
            });
            if (val !== undefined) {
              const r = await post(baseUrl, '/api/backend', { kind: 'local', port: +val });
              if (r && r.port_changed_requires_restart) vscode.window.showWarningMessage('端口已改，需重启代理 / 会话才生效');
            }
            break;
          }
          case 'temp':
          case 'topp': {
            const cur = provider.status && provider.status.ollama ? provider.status.ollama[action.kind] : undefined;
            const val = await vscode.window.showInputBox({
              value: cur !== undefined ? String(cur) : '',
              prompt: `输入 ${action.kind}（0-1 数值）`,
              validateInput: (v) => isNaN(+v) ? '必须是数字' : null,
            });
            if (val !== undefined) await post(baseUrl, '/api/config', { ollama: { [action.kind]: +val } });
            break;
          }
          case 'upstream': {
            const cur = provider.status ? provider.status.upstream : '';
            const val = await vscode.window.showInputBox({ value: cur || '', prompt: '输入上游地址（纯文本模型真实端点）' });
            if (val !== undefined && val.trim()) await post(baseUrl, '/api/config', { upstream: val.trim() });
            break;
          }
          case 'provider':
            if (action.provider) await post(baseUrl, '/api/backend', { kind: 'cloud', provider: action.provider });
            break;
        }
      } catch (e) {
        vscode.window.showErrorMessage('操作失败: ' + ((e && e.message) || e));
      }
      provider.refresh();
    }),
    vscode.commands.registerCommand('vision.refresh', () => provider.refresh())
  );
  // 每 5s 自动刷新（轻量，仅读状态；异常不外抛）
  const timer = setInterval(() => { try { provider.refresh(); } catch (e) {} }, 5000);
  context.subscriptions.push({ dispose: () => clearInterval(timer) });
}

exports.activate = activate;
function deactivate() {}
exports.deactivate = deactivate;
