/* ══════════════════════════════════════════════════════════════════════════════
   AlphaForge DAG Editor — Interactive JS Engine
   ══════════════════════════════════════════════════════════════════════════════ */

// ── 노드 타입 레지스트리 ──
const NODE_TYPES = [
  // 소스
  { type: 'universe',         name: '국장 종목',          desc: '한국 KOSPI / KOSDAQ ~2,700종목',   category: 'source', icon: '🌐',
    params: [{ key: 'market', label: 'MARKET', desc: '시장 선택 (ALL/KOSPI/KOSDAQ)', type: 'select', options: ['ALL','KOSPI','KOSDAQ'], default: 'ALL' }],
    arity: 0, outputs: ['market_cap'] },

  // 시그널
  { type: 'vcp',              name: 'VCP 패턴 찾기',      desc: '변동성 축소하는 변동성 수축',       category: 'signal', icon: '📉',
    params: [{ key: 'lookback_days', label: 'LOOKBACK_DAYS', desc: '과거 참조 기간', type: 'number', default: 120 }],
    arity: 1, outputs: ['vcp_score'] },
  { type: 'box_breakout',     name: '박스권 돌파 찾기',    desc: 'N일 고점 위로 돌파',               category: 'signal', icon: '📈',
    params: [
      { key: 'box_period', label: 'BOX_PERIOD', desc: '박스 기간', type: 'number', default: 60 },
      { key: 'breakout_pct', label: 'BREAKOUT_PCT', desc: '돌파 비율 (%)', type: 'number', default: 1.0 }
    ],
    arity: 1, outputs: ['box_breakout_pct'] },
  { type: 'ma_alignment',     name: '이평선 정배열 찾기',  desc: '5일 > 20일 > 60일',               category: 'signal', icon: '〰️',
    params: [],
    arity: 1, outputs: [] },
  { type: 'foreign_flow',     name: '외국인 매수 찾기',    desc: '한국 — 외국인 순매수 누적',        category: 'signal', icon: '🏦',
    params: [{ key: 'n_days', label: 'N_DAYS', desc: '누적 기간 (거래일)', type: 'number', default: 5 }],
    arity: 1, outputs: ['foreign_net_buy'] },
  { type: 'institution_flow', name: '기관 매수 찾기',      desc: '한국 — 기관 순매수 누적량',        category: 'signal', icon: '🏛️',
    params: [{ key: 'n_days', label: 'N_DAYS', desc: '누적 기간', type: 'number', default: 5 }],
    arity: 1, outputs: ['institution_net_buy'] },

  // 필터
  { type: 'and_filter',       name: '둘 다 통과한 종목 (AND)', desc: '교집합 필터',                 category: 'filter', icon: '∩',
    params: [],
    arity: 2, outputs: [] },
  { type: 'or_filter',        name: '하나라도 통과 (OR)',    desc: '합집합 필터',                    category: 'filter', icon: '∪',
    params: [],
    arity: 2, outputs: [] },
  { type: 'score_filter',     name: '점수로 거르기',        desc: '점수 임계 통과',                  category: 'filter', icon: '🎯',
    params: [
      { key: 'score_column', label: 'SCORE_COLUMN', desc: '점수 기준 컬럼', type: 'text', default: 'close' },
      { key: 'threshold', label: 'THRESHOLD', desc: '임계값', type: 'number', default: 0.6 },
      { key: 'greater_than', label: 'GREATER_THAN', desc: '이상(true)/이하(false)', type: 'select', options: ['true','false'], default: 'true' }
    ],
    arity: 1, outputs: [] },
  { type: 'top_n',            name: '묶고 거르기',          desc: '점수로 줄이고 결과 합치기',       category: 'filter', icon: '🏆',
    params: [
      { key: 'sort_column', label: 'SORT_COLUMN', desc: '정렬 기준', type: 'text', default: 'market_cap' },
      { key: 'n', label: 'N', desc: '최대 종목 수', type: 'number', default: 50 },
      { key: 'ascending', label: 'ASCENDING', desc: '오름차순', type: 'select', options: ['true','false'], default: 'false' }
    ],
    arity: 1, outputs: [] },

  // AI
  { type: 'ai_analysis',      name: 'AI에게 묻기',         desc: 'LLM에 종목 분석 코멘트',          category: 'ai', icon: '🤖',
    params: [{ key: 'max_tokens_per_stock', label: 'MAX_TOKENS', desc: '종목당 토큰', type: 'number', default: 256 }],
    arity: 1, outputs: ['score','grade','comment'] },
  { type: 'news_search',      name: '최근 뉴스 검색',      desc: 'Perplexity로 종목별 24-48h 뉴스', category: 'ai', icon: '📰',
    params: [{ key: 'max_news_count', label: 'MAX_NEWS', desc: '최대 뉴스 수', type: 'number', default: 3 }],
    arity: 1, outputs: ['recent_news'] },
];

const CATEGORY_LABELS = { source: '📦 종목 가져오기', signal: '📊 시그널 찾기', filter: '🔧 필터 & 결합', ai: '🤖 AI & 외부' };
const CATEGORY_ORDER = ['source', 'signal', 'filter', 'ai'];

// ── State ──
let nodes = [];
let edges = [];
let nextNodeId = 1;
let selectedNodeId = null;
let zoom = 1;
let panX = 0, panY = 0;
let draggingNode = null;
let dragOffset = { x: 0, y: 0 };
let connectingFrom = null; // { nodeId, port: 'output' }

// ── Init ──
document.addEventListener('DOMContentLoaded', () => {
  renderToolList();
  setupCanvasEvents();
  setupTopbar();
  setupTabs();
  setupKeyboard();
  updateToolCount();
  loadDefaultPipeline();
});

// ══ TOOL LIST ══
function renderToolList(filter = '') {
  const list = document.getElementById('toolList');
  list.innerHTML = '';
  const lower = filter.toLowerCase();

  for (const cat of CATEGORY_ORDER) {
    const items = NODE_TYPES.filter(n => n.category === cat && (n.name.toLowerCase().includes(lower) || n.desc.toLowerCase().includes(lower)));
    if (items.length === 0) continue;

    const header = document.createElement('div');
    header.className = 'tool-category-header';
    header.textContent = CATEGORY_LABELS[cat];
    list.appendChild(header);

    for (const nt of items) {
      const el = document.createElement('div');
      el.className = 'tool-item';
      el.dataset.type = nt.type;
      el.innerHTML = `
        <div class="tool-icon ${nt.category}">${nt.icon}</div>
        <div class="tool-info">
          <div class="tool-name">${nt.name}</div>
          <div class="tool-desc">${nt.desc}</div>
        </div>`;
      el.addEventListener('dblclick', () => addNodeToCanvas(nt.type));
      el.draggable = true;
      el.addEventListener('dragstart', e => {
        e.dataTransfer.setData('text/plain', nt.type);
      });
      list.appendChild(el);
    }
  }
}

function updateToolCount() {
  document.getElementById('toolCount').textContent = NODE_TYPES.length + '개';
}

document.getElementById('toolSearch').addEventListener('input', e => renderToolList(e.target.value));

// ══ CANVAS EVENTS ══
function setupCanvasEvents() {
  const canvas = document.getElementById('canvas');
  const area = document.getElementById('canvas-area');

  // Drop from toolbox
  area.addEventListener('dragover', e => e.preventDefault());
  area.addEventListener('drop', e => {
    e.preventDefault();
    const type = e.dataTransfer.getData('text/plain');
    if (!type) return;
    const rect = area.getBoundingClientRect();
    const x = (e.clientX - rect.left) / zoom - panX;
    const y = (e.clientY - rect.top) / zoom - panY;
    addNodeToCanvas(type, x, y);
  });

  // Click background to deselect
  area.addEventListener('click', e => {
    if (e.target === area || e.target.id === 'canvas' || e.target.id === 'nodeLayer') {
      selectNode(null);
    }
  });

  // Zoom
  document.getElementById('zoomIn').addEventListener('click', () => setZoom(zoom + 0.1));
  document.getElementById('zoomOut').addEventListener('click', () => setZoom(zoom - 0.1));
  document.getElementById('zoomFit').addEventListener('click', () => setZoom(1));
  area.addEventListener('wheel', e => {
    e.preventDefault();
    setZoom(zoom + (e.deltaY > 0 ? -0.05 : 0.05));
  }, { passive: false });
}

function setZoom(z) {
  zoom = Math.max(0.3, Math.min(2, z));
  const canvas = document.getElementById('canvas');
  canvas.style.transform = `scale(${zoom})`;
  document.getElementById('zoomLevel').textContent = Math.round(zoom * 100) + '%';
}

// ══ ADD NODE ══
function addNodeToCanvas(type, x, y) {
  const nt = NODE_TYPES.find(n => n.type === type);
  if (!nt) return;

  const id = 'n' + nextNodeId++;
  const node = {
    id,
    type: nt.type,
    name: nt.name,
    desc: nt.desc,
    category: nt.category,
    icon: nt.icon,
    arity: nt.arity,
    x: x || 100 + Math.random() * 300,
    y: y || 80 + Math.random() * 200,
    params: {},
    status: 'idle',   // idle / running / ok / error
    inputCount: 0,
    outputCount: 0,
  };

  // Init default params
  for (const p of nt.params) {
    node.params[p.key] = p.default;
  }

  nodes.push(node);
  renderNode(node);
  selectNode(id);
  renderEdges();
}

function renderNode(node) {
  const layer = document.getElementById('nodeLayer');

  // Remove if already exists
  const existing = document.getElementById(node.id);
  if (existing) existing.remove();

  const el = document.createElement('div');
  el.className = `dag-node ${node.status === 'ok' ? 'done' : ''} ${selectedNodeId === node.id ? 'selected' : ''}`;
  el.id = node.id;
  el.style.left = node.x + 'px';
  el.style.top = node.y + 'px';

  const statusClass = node.status === 'ok' ? 'ok' : node.status === 'error' ? 'error' : node.status === 'running' ? 'running' : 'idle';
  const statusText = node.status === 'ok' ? 'OK' : node.status === 'error' ? 'ERR' : node.status === 'running' ? 'RUN' : 'IDLE';

  el.innerHTML = `
    <div class="node-header">
      <div class="node-title"><span class="dot ${node.category}"></span>${node.name}</div>
      <span class="node-status-badge ${statusClass}">${statusText}</span>
    </div>
    <div class="node-subtitle">${node.desc}</div>
    <div class="node-counts">
      ${node.inputCount > 0 ? `<span class="count-in">${node.inputCount}</span>` : ''}
      ${node.inputCount > 0 || node.outputCount > 0 ? '<span class="count-arrow">→</span>' : ''}
      ${node.outputCount > 0 ? `<span class="count-out">${node.outputCount}</span>` : ''}
    </div>
    ${node.arity > 0 ? '<div class="port input" data-port="input" data-node="' + node.id + '"></div>' : ''}
    <div class="port output" data-port="output" data-node="${node.id}"></div>
    ${node.label ? `<div class="node-label">${node.label}</div>` : ''}
  `;

  // Select on click (not on ports)
  el.addEventListener('mousedown', e => {
    if (e.target.classList.contains('port')) return;
    selectNode(node.id);
    draggingNode = node;
    dragOffset = { x: e.clientX / zoom - node.x, y: e.clientY / zoom - node.y };
    e.stopPropagation();
  });

  // Output port: 클릭하면 연결 모드 시작
  const outPort = el.querySelector('.port.output');
  if (outPort) {
    outPort.addEventListener('click', e => {
      e.stopPropagation();
      e.preventDefault();
      startConnecting(node.id);
    });
  }

  // Input port: 연결 모드 중이면 연결 완료
  const inPort = el.querySelector('.port.input');
  if (inPort) {
    inPort.addEventListener('click', e => {
      e.stopPropagation();
      e.preventDefault();
      if (connectingFrom && connectingFrom !== node.id) {
        addEdge(connectingFrom, node.id);
        stopConnecting();
      }
    });
  }

  layer.appendChild(el);
}

// ══ CONNECT MODE ══
function startConnecting(fromNodeId) {
  connectingFrom = fromNodeId;
  document.body.classList.add('connecting-mode');
  // 모든 input 포트를 강조
  document.querySelectorAll('.port.input').forEach(p => {
    if (p.dataset.node !== fromNodeId) p.classList.add('connect-target');
  });
  // 출발 포트 강조
  const fromEl = document.getElementById(fromNodeId);
  if (fromEl) fromEl.querySelector('.port.output')?.classList.add('connect-source');
  // 배너 표시
  showConnectBanner(fromNodeId);
}

function stopConnecting() {
  connectingFrom = null;
  document.body.classList.remove('connecting-mode');
  document.querySelectorAll('.port').forEach(p => {
    p.classList.remove('connect-target');
    p.classList.remove('connect-source');
  });
  hideConnectBanner();
}

function showConnectBanner(fromId) {
  let banner = document.getElementById('connectBanner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'connectBanner';
    document.getElementById('canvas-area').appendChild(banner);
  }
  const node = nodes.find(n => n.id === fromId);
  banner.innerHTML = `🔗 <strong>${node?.name || fromId}</strong>의 출력을 연결할 노드의 <span style="color:var(--purple);font-weight:700;">보라색 포트</span>를 클릭하세요 &nbsp; <button onclick="stopConnecting()">✕ 취소</button>`;
  banner.style.display = 'flex';
}

function hideConnectBanner() {
  const banner = document.getElementById('connectBanner');
  if (banner) banner.style.display = 'none';
}

// Global mouse events for dragging nodes
document.addEventListener('mousemove', e => {
  if (draggingNode) {
    draggingNode.x = e.clientX / zoom - dragOffset.x;
    draggingNode.y = e.clientY / zoom - dragOffset.y;
    const el = document.getElementById(draggingNode.id);
    if (el) {
      el.style.left = draggingNode.x + 'px';
      el.style.top = draggingNode.y + 'px';
    }
    renderEdges();
  }
});
document.addEventListener('mouseup', () => {
  draggingNode = null;
});

// 캔버스 빈 곳 클릭 시 연결 모드 취소
document.addEventListener('click', e => {
  if (connectingFrom && !e.target.classList.contains('port') && !e.target.closest('#connectBanner')) {
    stopConnecting();
  }
});

// ══ DELETE NODE ══
function deleteNode(nodeId) {
  nodes = nodes.filter(n => n.id !== nodeId);
  edges = edges.filter(e => e.from !== nodeId && e.to !== nodeId);
  const el = document.getElementById(nodeId);
  if (el) el.remove();
  if (selectedNodeId === nodeId) selectNode(null);
  renderEdges();
}

function deleteSelectedNode() {
  if (selectedNodeId) deleteNode(selectedNodeId);
}

function setupKeyboard() {
  document.addEventListener('keydown', e => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return;
    if ((e.key === 'Delete' || e.key === 'Backspace') && selectedNodeId) {
      e.preventDefault();
      deleteSelectedNode();
    }
    if (e.key === 'Escape' && connectingFrom) {
      stopConnecting();
    }
  });
}

// ══ EDGES ══
function addEdge(fromId, toId) {
  // 중복 방지
  if (edges.find(e => e.from === fromId && e.to === toId)) return;
  edges.push({ from: fromId, to: toId });
  renderEdges();
}

function renderEdges() {
  const svg = document.getElementById('edgeSvg');
  svg.innerHTML = '';

  for (const edge of edges) {
    const fromEl = document.getElementById(edge.from);
    const toEl = document.getElementById(edge.to);
    if (!fromEl || !toEl) continue;

    const fromNode = nodes.find(n => n.id === edge.from);
    const toNode = nodes.find(n => n.id === edge.to);
    if (!fromNode || !toNode) continue;

    const x1 = fromNode.x + fromEl.offsetWidth;
    const y1 = fromNode.y + fromEl.offsetHeight / 2;
    const x2 = toNode.x;
    const y2 = toNode.y + toEl.offsetHeight / 2;

    const dx = Math.abs(x2 - x1) * 0.5;
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`);
    path.setAttribute('class', 'edge-path');
    svg.appendChild(path);
  }
}

// ══ SELECT NODE ══
function selectNode(id) {
  selectedNodeId = id;

  // Update node visuals
  document.querySelectorAll('.dag-node').forEach(el => el.classList.remove('selected'));
  if (id) {
    const el = document.getElementById(id);
    if (el) el.classList.add('selected');
  }

  // Update right sidebar
  const node = nodes.find(n => n.id === id);
  const title = document.getElementById('selectedNodeTitle');
  const meta = document.getElementById('selectedNodeMeta');
  const suggested = document.getElementById('suggestedNodes');

  if (!node) {
    title.textContent = '노드를 선택하세요';
    meta.style.display = 'none';
    suggested.style.display = 'none';
    document.getElementById('paramsForm').innerHTML = '<p class="empty-msg">노드 선택 필요</p>';
    document.getElementById('resultView').innerHTML = '<p class="empty-msg">실행 결과 없음</p>';
    document.getElementById('yamlView').textContent = '노드 선택 필요';
    return;
  }

  title.textContent = `● ${node.name}`;
  meta.style.display = 'flex';
  document.getElementById('selectedNodeType').textContent = node.type;
  document.getElementById('selectedNodeStatus').textContent = `● ${node.status}`;

  // Suggested nodes
  suggested.style.display = 'block';
  const tags = document.getElementById('suggestedTags');
  const suggestions = getSuggestions(node);
  tags.innerHTML = suggestions.map(s =>
    `<span class="suggest-tag ${s.accent || ''}" data-type="${s.type}">${s.name}</span>`
  ).join('');
  tags.querySelectorAll('.suggest-tag').forEach(el => {
    el.addEventListener('click', () => {
      const newNode = addNodeToCanvas(el.dataset.type, node.x + 280, node.y);
    });
  });

  // Params form
  renderParamsForm(node);

  // Result
  renderResult(node);

  // YAML
  renderYaml(node);
}

function getSuggestions(node) {
  const all = NODE_TYPES.filter(nt => nt.arity > 0 && nt.type !== node.type);
  return all.slice(0, 4).map(nt => ({ type: nt.type, name: nt.name, accent: '' }));
}

function renderParamsForm(node) {
  const nt = NODE_TYPES.find(n => n.type === node.type);
  const form = document.getElementById('paramsForm');

  if (!nt || nt.params.length === 0) {
    form.innerHTML = '<p class="empty-msg">이 노드는 설정 파라미터가 없습니다.</p>';
    return;
  }

  form.innerHTML = nt.params.map(p => {
    const val = node.params[p.key] ?? p.default;
    if (p.type === 'select') {
      const opts = p.options.map(o => `<option value="${o}" ${String(val) === String(o) ? 'selected' : ''}>${o}</option>`).join('');
      return `<div class="param-group">
        <label>${p.label}</label>
        <div class="param-desc">${p.desc}</div>
        <select data-key="${p.key}">${opts}</select>
      </div>`;
    }
    return `<div class="param-group">
      <label>${p.label}</label>
      <div class="param-desc">${p.desc}</div>
      <input type="${p.type === 'number' ? 'number' : 'text'}" value="${val}" data-key="${p.key}" step="any">
    </div>`;
  }).join('');

  form.querySelectorAll('input, select').forEach(el => {
    el.addEventListener('change', () => {
      node.params[el.dataset.key] = el.type === 'number' ? parseFloat(el.value) : el.value;
      renderYaml(node);
    });
  });
}

function renderResult(node) {
  const view = document.getElementById('resultView');
  if (node.status !== 'ok' || !node.resultData || node.resultData.length === 0) {
    view.innerHTML = `<p class="empty-msg">${node.error || '실행 결과 없음'}</p>`;
    return;
  }
  const data = node.resultData;
  const cols = node.resultColumns || Object.keys(data[0] || {});
  const priority = ['code','name','market','close','volume','market_cap','foreign_net_buy','institution_net_buy','vcp_score','box_breakout_pct'];
  const display = priority.filter(c => cols.includes(c));
  cols.forEach(c => { if (!display.includes(c)) display.push(c); });
  const shown = display.slice(0, 8);

  view.innerHTML = `
    <div class="result-header">
      <span style="font-weight:600;">${node.totalCount || data.length}개 종목 (상위 ${data.length}개)</span>
      <button class="excel-btn">📥 Excel</button>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:8px;font-size:10px;color:var(--text-faint);">
      <span>⏱ ${node.latencyMs?.toFixed(0) || '—'}ms</span>
      <span>${node.cacheHit ? '💾 캐시' : '🔄 실시간'}</span>
    </div>
    <div style="overflow-x:auto;">
      <table class="result-table">
        <thead><tr>${shown.map(c => `<th>${c}</th>`).join('')}</tr></thead>
        <tbody>
          ${data.map(row => `<tr>${shown.map(c => {
            let v = row[c];
            if (v == null) v = '—';
            else if (typeof v === 'number') v = Math.abs(v) > 999 ? v.toLocaleString() : (Number.isInteger(v) ? v : v.toFixed(2));
            return `<td>${v}</td>`;
          }).join('')}</tr>`).join('')}
        </tbody>
      </table>
    </div>
  `;
}

function renderYaml(node) {
  const yaml = document.getElementById('yamlView');
  const lines = [`id: ${node.id}`, `type: ${node.type}`, `name: ${node.name}`];
  if (Object.keys(node.params).length > 0) {
    lines.push('params:');
    for (const [k, v] of Object.entries(node.params)) {
      lines.push(`  ${k}: ${v}`);
    }
  }
  const incoming = edges.filter(e => e.to === node.id).map(e => e.from);
  if (incoming.length > 0) {
    lines.push('inputs:');
    incoming.forEach(id => lines.push(`  - ${id}`));
  }
  yaml.textContent = lines.join('\n');
}

// ══ TABS ══
function setupTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById('tab' + capitalize(btn.dataset.tab)).classList.add('active');
    });
  });
}
function capitalize(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

// ══ TOPBAR ══
function setupTopbar() {
  document.getElementById('btnStart').addEventListener('click', runPipeline);
  document.getElementById('btnNew').addEventListener('click', () => {
    if (confirm('현재 파이프라인을 초기화하시겠습니까?')) {
      nodes = [];
      edges = [];
      selectedNodeId = null;
      document.getElementById('nodeLayer').innerHTML = '';
      renderEdges();
      selectNode(null);
      resetStatus();
    }
  });
}

// ══ PIPELINE EXECUTION (REAL BACKEND) ══
async function runPipeline() {
  const btn = document.getElementById('btnStart');
  if (nodes.length === 0) return alert('노드를 추가하세요.');

  btn.textContent = '⏳ 실행중...';
  btn.classList.add('running');
  nodes.forEach(n => { n.status = 'running'; renderNode(n); });
  renderEdges();

  const dagDef = {
    nodes: nodes.map(n => ({ id: n.id, type: n.type, params: n.params })),
    edges: edges.map(e => ({ from: e.from, to: e.to }))
  };

  try {
    const t0 = performance.now();
    const resp = await fetch('/api/execute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(dagDef)
    });
    const result = await resp.json();
    const elapsed = performance.now() - t0;

    if (!result.success) {
      alert('실행 실패: ' + (result.error || '알 수 없는 오류'));
      nodes.forEach(n => { n.status = 'error'; renderNode(n); });
      renderEdges();
      return;
    }

    let idx = 0;
    const total = Object.keys(result.node_results).length;
    for (const [nodeId, nr] of Object.entries(result.node_results)) {
      const node = nodes.find(n => n.id === nodeId);
      if (!node) continue;
      const st = nr.status || '';
      node.status = (st === 'ok' || st === 'cache_hit' || st === 'success' || st === 'cached') ? 'ok' : (st === 'error' || st === 'skipped') ? 'error' : st;
      node.outputCount = nr.total_count || nr.output_count || 0;
      node.inputCount = nr.input_count || 0;
      node.resultData = nr.data || [];
      node.resultColumns = nr.columns || [];
      node.totalCount = nr.total_count || 0;
      node.latencyMs = nr.latency_ms || 0;
      node.cacheHit = nr.cache_hit || false;
      node.error = nr.error || null;
      if (node.type === 'universe') node.label = 'universe';
      idx++;
      renderNode(node);
      document.getElementById('progressLabel').textContent = `Progress ${idx}/${total}`;
      document.getElementById('progressFill').style.width = `${(idx / total) * 100}%`;
    }
    renderEdges();

    // 결과에 없는 노드(고아 노드)는 idle로 복원
    const resultIds = new Set(Object.keys(result.node_results));
    nodes.forEach(n => {
      if (!resultIds.has(n.id) && n.status === 'running') {
        n.status = 'idle';
        n.outputCount = 0;
        n.inputCount = 0;
        renderNode(n);
      }
    });
    renderEdges();

    // 마지막 노드 선택 및 결과 표시
    const lastId = Object.keys(result.node_results).pop();
    if (lastId) { selectNode(lastId); switchToTab('result'); }

    document.getElementById('runId').textContent = result.run_id || '';
    document.getElementById('statLatency').textContent = `${Math.round(elapsed)}ms`;
    document.getElementById('statCost').textContent = '$0.0000';

  } catch (err) {
    alert('API 호출 실패: ' + err.message);
    nodes.forEach(n => { n.status = 'error'; renderNode(n); });
  } finally {
    btn.textContent = '▶ Start';
    btn.classList.remove('running');
    renderEdges();
  }
}

function topologicalSort() {
  const inDeg = {};
  const adj = {};
  nodes.forEach(n => { inDeg[n.id] = 0; adj[n.id] = []; });
  edges.forEach(e => { inDeg[e.to]++; adj[e.from].push(e.to); });
  const queue = nodes.filter(n => inDeg[n.id] === 0).map(n => n.id);
  const result = [];
  while (queue.length > 0) {
    const curr = queue.shift();
    result.push(curr);
    for (const next of adj[curr]) {
      inDeg[next]--;
      if (inDeg[next] === 0) queue.push(next);
    }
  }
  return result;
}

function switchToTab(tabName) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  const btn = document.querySelector(`.tab-btn[data-tab="${tabName}"]`);
  const content = document.getElementById('tab' + capitalize(tabName));
  if (btn) btn.classList.add('active');
  if (content) content.classList.add('active');
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function resetStatus() {
  document.getElementById('runId').textContent = '대기 중';
  document.getElementById('progressLabel').textContent = 'Progress 0/0';
  document.getElementById('progressFill').style.width = '0%';
  document.getElementById('statLatency').textContent = '—';
}

// ══ DEFAULT PIPELINE ══
function loadDefaultPipeline() {
  // Pre-create a demo pipeline
  addNodeToCanvas('universe', 120, 140);
  addNodeToCanvas('vcp', 420, 80);
  addNodeToCanvas('foreign_flow', 420, 280);
  addNodeToCanvas('and_filter', 700, 180);
  addNodeToCanvas('score_filter', 940, 180);
  addNodeToCanvas('top_n', 1160, 180);

  // Connect them
  setTimeout(() => {
    addEdge('n1', 'n2');
    addEdge('n1', 'n3');
    addEdge('n2', 'n4');
    addEdge('n3', 'n4');
    addEdge('n4', 'n5');
    addEdge('n5', 'n6');
    selectNode(null);
  }, 100);
}
