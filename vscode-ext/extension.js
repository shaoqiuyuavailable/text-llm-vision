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

// 场景/引擎显示名映射：router 行渲染成可读中文（未知键回退原文）
const SCENE_MAIN_LABELS = {
  person: '人物', animal: '动物', plant: '植物', food: '食物', vehicle: '交通工具',
  machine: '机器', architecture: '建筑', document: '文档', chart: '图表',
  diagram: '图示', map: '地图', screenshot: '截图', object: '物品',
  meme: '梗图', scene: '场景', unknown: '无法分类', generic: '通用',
};
const SCENE_SUB_LABELS = {
  chat: '聊天', code: '代码', report: '报告', form: '表单', table: '表格', email: '邮件', unknown: '未知',
  real_single: '单人', real_group: '多人', anime_character: '动漫角色', game_character: '游戏角色',
  cosplay: '扮演', statue: '雕像', painting: '绘画',
  mammal: '哺乳', bird: '鸟类', reptile: '爬行', amphibian: '两栖', fish: '鱼类', insect: '昆虫',
  flower: '花', tree: '树', fruit: '水果', vegetable: '蔬菜', succulent: '多肉', garden: '花园',
  dish: '菜肴', beverage: '饮品', snack: '零食', ingredient: '食材', dessert: '甜品', tableware: '餐具',
  car: '汽车', motorcycle: '摩托', truck: '卡车', bus: '公交', train: '火车', airplane: '飞机',
  ship: '船', bicycle: '单车',
  industrial: '工业', household: '家用', electronics: '电子', tool: '工具', construction: '施工',
  building: '建筑', interior: '室内', landmark: '地标', bridge: '桥梁', ruins: '遗迹',
  line: '折线', bar: '柱状', pie: '饼图', scatter: '散点', radar: '雷达', heatmap: '热力',
  flowchart: '流程', org_chart: '架构', network: '网络', sequence: '时序', gantt: '甘特', venn: '韦恩',
  road: '道路', satellite: '卫星', floor_plan: '平面图', topographic: '地形', subway: '地铁', world: '世界',
  software_ui: '软件界面', website: '网页', terminal: '终端', error: '报错', settings: '设置',
  product: '产品', clothing: '衣物', furniture: '家具', book: '书籍', toy: '玩具',
  template: '模板', text_overlay: '文字梗', reaction: '表情包', caption: '字幕',
  landscape: '风景', cityscape: '城市', indoor: '室内', nature: '自然', sky: '天空', weather: '天气',
};
const ENGINE_LABELS = {
  ocr: 'OCR 文字提取', vlm: '视觉识别', table: '表格解析', gui: '界面元素定位',
  code: '代码逐字提取', rapidtable: '表格解析', omniparser: '界面元素定位',
};

function sceneLabel(key) {
  if (key === '_default') return '其他';
  const [main, sub] = String(key).split('.');
  const ml = SCENE_MAIN_LABELS[main] || main;
  return sub ? `${ml}·${SCENE_SUB_LABELS[sub] || sub}` : ml;
}
function engineLabel(val) {
  const eng = String(val || '').split(':')[0];
  return ENGINE_LABELS[eng] || eng || '视觉识别';
}

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

// 读 Claude Code 用户级 MCP 注册，判断 vision 是 python(mcp_server.py) 还是旧 node(mcp-vision.js)
function readMcpStatus() {
  try {
    const raw = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.claude.json'), 'utf8'));
    const vision = (raw.mcpServers || {})['vision'];
    if (!vision) return { state: 'none', command: '' };
    const text = JSON.stringify(vision);
    if (text.includes('mcp_server.py')) return { state: 'python', command: text };
    if (text.includes('mcp-vision.js')) return { state: 'old-node', command: text };
    return { state: 'other', command: text };
  } catch (e) {
    return { state: 'unknown', command: '' };
  }
}

// 改绑场景模型：写 config.json router[scene]（顺带确保 prompts_version=2，让配置在 config_loader 门内生效）
function setRouterScene(scene, value) {
  const p = path.join(DEPLOY_DIR(), 'config.json');
  const raw = JSON.parse(fs.readFileSync(p, 'utf8'));
  raw.router = raw.router || {};
  raw.router[scene] = value;
  if (!raw.prompts_version) raw.prompts_version = 2;
  fs.writeFileSync(p, JSON.stringify(raw, null, 2));
}

// 执行 toggle.py model 子命令（下载/删除/替换），detached 不弹窗
function runModelCmd(args) {
  return new Promise((resolve) => {
    const toggle = path.join(DEPLOY_DIR(), 'toggle.py');
    if (!fs.existsSync(toggle)) {
      vscode.window.showErrorMessage('未找到 toggle.py（先运行 install.py 部署）');
      return resolve();
    }
    const pythonCmd = process.platform === 'win32' ? 'python' : 'python3';
    const child = spawn(pythonCmd, [toggle, 'model', ...args], {
      cwd: path.dirname(toggle), windowsHide: true, detached: true, stdio: 'ignore',
    });
    child.on('error', (err) => vscode.window.showErrorMessage('模型操作失败: ' + err.message));
    child.on('exit', (code) => vscode.window.showInformationMessage(code === 0 ? '模型操作完成' : `模型操作结束，退出码 ${code}`));
    child.unref();
    setTimeout(resolve, 2000); // 等操作落地再刷新
  });
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
    // ── MCP 主路径（模型主动看图）──
    const mcp = readMcpStatus();
    items.push(new VisionTreeItem('── MCP 主路径 ──', 'header', ''));
    if (mcp.state === 'python') {
      items.push(new VisionTreeItem('MCP: mcp_server.py ✓', 'mcpOk', 'Python server（主路径）'));
      items.push(new VisionTreeItem('工具: describe_image · extract_text · locate_object · compare_images · vision_rules', 'info', ''));
    } else if (mcp.state === 'old-node') {
      items.push(new VisionTreeItem('MCP: mcp-vision.js（旧 node）', 'mcpOld', '点击迁移到 mcp_server.py', { kind: 'mcpMigrate' }));
    } else if (mcp.state === 'none') {
      items.push(new VisionTreeItem('MCP: 未注册', 'mcpNone', '点击注册', { kind: 'mcpMigrate' }));
    } else if (mcp.state === 'other') {
      items.push(new VisionTreeItem('MCP: 其他 command', 'mcpOther', '非本项目脚本，保留'));
    } else {
      items.push(new VisionTreeItem('MCP: 无法读取 ~/.claude.json', 'info', ''));
    }
    items.push(new VisionTreeItem(`端口: ${d.port}`, 'port', '点击修改', { kind: 'port' }));
    items.push(new VisionTreeItem(`温度: ${d.ollama && d.ollama.temperature !== undefined ? d.ollama.temperature : '-'}`, 'temp', '点击修改', { kind: 'temp' }));
    items.push(new VisionTreeItem(`top_p: ${d.ollama && d.ollama.top_p !== undefined ? d.ollama.top_p : '-'}`, 'topp', '点击修改', { kind: 'topp' }));
    items.push(new VisionTreeItem(`上游(Anthropic): ${d.upstream || '-'}`, 'upstream', '点击修改', { kind: 'upstream' }));
    items.push(new VisionTreeItem(`上游(OpenAI): ${d.upstream_openai || '(未配置)'}`, 'upstream_openai', '点击修改', { kind: 'upstream_openai' }));
    items.push(new VisionTreeItem(`grounding: ${d.ollama && d.ollama.grounding ? '开' : '关'}`, 'grounding', '点击切换', { kind: 'grounding' }));
    items.push(new VisionTreeItem('── 云端厂商 ──', 'header', ''));
    (d.cloud || []).forEach((c) => {
      items.push(new VisionTreeItem(`${c.name}: ${c.model || '(未配model)'} key=${c.has_key ? '✓' : '✗'}`,
        'provider', c.name === d.active_provider ? '当前' : '点击切换', { kind: 'provider', provider: c.name }));
    });
    // ── 模型管理（v1.5 多路由模型：本地 + 云端，按场景配模型，可交互）──
    // 生效视图来自 /api/status（基线 + config 覆盖），config.json 无 router/models 时仍显示基线可交互
    const models = d.models || {};
    const router = d.router || {};
    items.push(new VisionTreeItem('── 模型管理 ──', 'header', ''));
    items.push(new VisionTreeItem('＋ 添加模型', 'modelAdd', '点击添加', { kind: 'modelAdd' }));
    const modelNames = Object.keys(models);
    if (modelNames.length === 0) {
      items.push(new VisionTreeItem('模型: 无（config.models 空）', 'info', '用上方「＋ 添加模型」'));
    }
    for (const name of modelNames) {
      const m = models[name];
      items.push(new VisionTreeItem(`模型: ${name} (${m.type}${m.provider ? '/' + m.provider : ''})`,
        'model', m.purpose || '点击操作', { kind: 'modelManage', model: name }));
    }
    items.push(new VisionTreeItem('场景 → 引擎映射', 'header', ''));
    // 每场景显示生效执行实体（来自 /api/status scene_exec）：ocr → RapidOCR(内置)；vlm → 实际模型
    const execMap = d.scene_exec || {};
    for (const [scene, val] of Object.entries(router)) {
      const ex = execMap[scene];
      const desc = (ex ? ex + ' · ' : '') + '点击改绑';
      items.push(new VisionTreeItem(`  ${sceneLabel(scene)} → ${engineLabel(val)}`, 'routerScene', desc, { kind: 'routerScene', scene, value: val }));
    }
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
          case 'mcpMigrate': {
            const inst = path.join(DEPLOY_DIR(), 'install.py');
            if (!fs.existsSync(inst)) {
              vscode.window.showErrorMessage('未找到 install.py（先运行 install.py 部署）');
              break;
            }
            const pythonCmd = process.platform === 'win32' ? 'python' : 'python3';
            const child = spawn(pythonCmd, [inst, '--mcp', 'claude'], {
              cwd: path.dirname(inst), windowsHide: true, detached: true, stdio: 'ignore',
            });
            child.on('error', (err) => vscode.window.showErrorMessage('MCP 注册失败: ' + err.message));
            child.on('exit', (code) => vscode.window.showInformationMessage(code === 0 ? 'MCP 已注册（mcp_server.py）' : `MCP 注册结束，退出码 ${code}`));
            child.unref();
            break;
          }
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
            const val = await vscode.window.showInputBox({ value: cur || '', prompt: '输入 Anthropic 上游（Claude Code 链路，纯文本模型真实端点）' });
            if (val !== undefined && val.trim()) await post(baseUrl, '/api/config', { upstream: val.trim() });
            break;
          }
          case 'upstream_openai': {
            const cur = provider.status ? provider.status.upstream_openai : '';
            const val = await vscode.window.showInputBox({ value: cur || '', prompt: '输入 OpenAI 上游基础 URL（Cline/OpenCode 链路，/v1/chat/completions 由它拼接）' });
            if (val !== undefined && val.trim()) await post(baseUrl, '/api/config', { upstream_openai: val.trim() });
            break;
          }
          case 'grounding': {
            const cur = provider.status && provider.status.ollama ? !!provider.status.ollama.grounding : true;
            const pick = await vscode.window.showQuickPick(
              [{ label: '开（支持 bbox 的模型）', value: true }, { label: '关（不支持 grounding 时跳过空间结构）', value: false }],
              { placeHolder: 'grounding 当前: ' + (cur ? '开' : '关') });
            if (pick) await post(baseUrl, '/api/config', { ollama: { grounding: pick.value } });
            break;
          }
          case 'provider':
            if (action.provider) await post(baseUrl, '/api/backend', { kind: 'cloud', provider: action.provider });
            break;
          case 'modelAdd': {
            const name = await vscode.window.showInputBox({ prompt: '模型名（如 llava:7b / qwen-vl-plus）' });
            if (!name || !name.trim()) break;
            const typePick = await vscode.window.showQuickPick(
              [{ label: 'ollama（本地）', value: 'ollama' }, { label: 'cloud（云端）', value: 'cloud' },
               { label: 'pip（如 rapid-table）', value: 'pip' }],
              { placeHolder: '模型类型' });
            if (!typePick) break;
            const args = ['add', name.trim(), '--type', typePick.value];
            if (typePick.value === 'cloud') {
              const provider = await vscode.window.showInputBox({ prompt: '云端厂商（config.cloud.clouds 里的名字，如 dashscope）' });
              if (!provider || !provider.trim()) break;
              args.push('--provider', provider.trim());
            }
            const purpose = await vscode.window.showInputBox({ prompt: '用途（可选，如 default/gui/table）' });
            if (purpose && purpose.trim()) args.push('--purpose', purpose.trim());
            const dl = await vscode.window.showQuickPick(
              [{ label: '下载', value: 'yes' }, { label: '暂不下载', value: 'no' }], { placeHolder: '添加后下载？' });
            if (dl && dl.value === 'yes') args.push('--download');
            await runModelCmd(args);
            break;
          }
          case 'routerScene': {
            const curTxt = action.value ? `${engineLabel(action.value)}（${action.value}）` : '未设置';
            const val = await vscode.window.showInputBox({
              value: action.value || '',
              prompt: `改绑「${sceneLabel(action.scene)}」，当前 ${curTxt}。` +
                      '输入 引擎:模型 或 引擎，如 vlm:qwen2.5vl / ocr（模型须已注册）',
            });
            if (val !== undefined && val.trim()) {
              try {
                setRouterScene(action.scene, val.trim());
                vscode.window.showInformationMessage(`场景 ${action.scene} 已改绑 → ${val.trim()}`);
              } catch (e) {
                vscode.window.showErrorMessage('写 config.json 失败: ' + ((e && e.message) || e));
              }
            }
            break;
          }
          case 'modelManage': {
            const pick = await vscode.window.showQuickPick(
              [{ label: '下载', value: 'download' }, { label: '逻辑删除', value: 'rm' },
               { label: '物理删除 (ollama rm)', value: 'rmp' }, { label: '替换', value: 'replace' }],
              { placeHolder: `操作模型 ${action.model}` });
            if (!pick) break;
            if (pick.value === 'replace') {
              const newName = await vscode.window.showInputBox({ prompt: '新模型名（建议先 model add 注册）' });
              if (newName && newName.trim()) await runModelCmd(['replace', action.model, newName.trim()]);
            } else if (pick.value === 'rm') {
              await runModelCmd(['rm', action.model]);
            } else if (pick.value === 'rmp') {
              const yes = await vscode.window.showQuickPick(
                [{ label: '确认物理删除（不可逆，释放磁盘）', value: 'yes' }, { label: '取消', value: 'no' }],
                { placeHolder: `物理删除 ${action.model} 将执行 ollama rm` });
              if (yes && yes.value === 'yes') await runModelCmd(['rm', action.model, '--physical', '--yes']);
            } else {
              await runModelCmd(['download', action.model]);
            }
            break;
          }
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
