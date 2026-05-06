// ══════════════════════════════════════════════════════════════
// AlphaForge Strategy Builder
// ══════════════════════════════════════════════════════════════

const SIGNALS = [
  { id: 'vcp', name: 'VCP 패턴 찾기', desc: '변동성 수축하는 패턴 필터', tag: 'signal',
    params: [{ key: 'lookback_days', label: '기간(일)', type: 'number', default: 120 }] },
  { id: 'box_breakout', name: '박스권 돌파', desc: 'N일 고점 돌파 종목', tag: 'signal',
    params: [
      { key: 'box_period', label: '박스기간', type: 'number', default: 60 },
      { key: 'breakout_pct', label: '돌파(%)', type: 'number', default: 1.0, step: 0.1 },
    ] },
  { id: 'ma_alignment', name: '이평선 정배열', desc: '5 > 20 > 60 이동평균선', tag: 'signal',
    params: [] },
  { id: 'rs_rating', name: '상대 강도(RS)', desc: '지수 대비 강한 종목 필터', tag: 'signal',
    params: [{ key: 'lookback_days', label: '기간(일)', type: 'number', default: 252 }] },
  { id: 'foreign_flow', name: '외국인 수급', desc: '외국인 순매수 및 장기 추세', tag: 'signal',
    params: [{ key: 'n_days', label: '기간(일)', type: 'number', default: 5 }] },
  { id: 'institution_flow', name: '기관 수급', desc: '기관 순매수', tag: 'signal',
    params: [{ key: 'n_days', label: '기간(일)', type: 'number', default: 5 }] },
  { id: 'sector', name: '섹터 분류', desc: '섹터 정보 매핑', tag: 'filter', params: [] },
  { id: 'macro_filter', name: '매크로 분석', desc: 'VIX/SP500 추세 분석', tag: 'filter', params: [] },
  { id: 'liquidity_filter', name: '유동성 필터', desc: '거래대금 부족 종목 제거', tag: 'filter',
    params: [{ key: 'min_trading_value_krw', label: '최소 거래대금(억)', type: 'number', default: 10, step: 1 }],
    transformParams: p => ({ min_trading_value_krw: (p.min_trading_value_krw || 10) * 1e8 }),
  },
  { id: 'score_filter', name: '최종 점수 종합', desc: '총점 및 Tier 산출', tag: 'filter',
    params: [],
  },
];

// ── Step 1: 시그널 메타데이터 ─────────────────────────────────────────────────
const SIGNAL_META = {
  vcp: {
    label: 'VCP 패턴 (변동성 수축)',
    desc: '거래량 감소와 함께 변동폭이 줄어드는 변동성 수축 패턴입니다. 큰 상승 직전 나타나는 조용한 매집 신호로, 돌파 시 빠른 상승이 나타나는 경우가 많습니다.',
    tip: '진입: 전고점 돌파 + 거래량 급증 확인 후 | 손절: 수축 저점 하향 이탈 시 | 목표: 이전 상승폭의 1~2배',
    col: 'vcp_score',
    makeTag: () => ({ label: 'VCP ✓', cls: 'ev-accent' }),
  },
  box_breakout: {
    label: '박스권 돌파',
    desc: 'N일간 고점(저항선)을 현재가로 돌파한 종목입니다. 저항이 지지로 전환되는 구간으로, 추세 전환 또는 모멘텀 가속의 강력한 신호입니다.',
    tip: '진입: 당일·익일 시가 또는 눌림목 | 손절: 박스 하단 이탈 시 | 목표: 박스 높이만큼 추가 상승',
    col: 'box_breakout_pct',
    makeTag: row => ({ label: `박스 +${Number(row.box_breakout_pct || 0).toFixed(1)}%`, cls: 'ev-green' }),
  },
  ma_alignment: {
    label: '이평선 정배열 (5>20>60)',
    desc: '5일 > 20일 > 60일 이동평균선이 모두 상승 방향으로 정렬된 종목입니다. 단기·중기·장기 추세가 일치하는 강한 상승 추세를 나타냅니다.',
    tip: '진입: 이평선 간격이 벌어지는 눌림목 반등 시 | 손절: 5일선 종가 이탈 시 | 주의: 과열 구간에서 진입 자제',
    col: null,
    makeTag: () => ({ label: '정배열 ✓', cls: 'ev-purple' }),
  },
  foreign_flow: {
    label: '외국인 순매수',
    desc: '외국인이 최근 N일간 누적 순매수 중입니다. 정보력 있는 스마트머니의 진입으로 해석되며, 중기 상승 모멘텀과 높은 상관관계를 보입니다.',
    tip: '주의: 환율 변동과 함께 확인 필요 | 순매수→순매도 반전 시 즉시 점검 | 연속 매수 일수도 확인',
    col: 'foreign_net_buy',
    makeTag: row => {
      const v = Number(row.foreign_net_buy || 0);
      return { label: v >= 0 ? `외국인 +${v.toLocaleString()}` : `외국인 ${v.toLocaleString()}`, cls: v >= 0 ? 'ev-green' : 'ev-red' };
    },
  },
  institution_flow: {
    label: '기관 순매수',
    desc: '기관이 최근 N일간 누적 순매수 중입니다. 기관의 지속적 매수는 중·장기 상승 추세를 지지하는 강한 수급 요인입니다.',
    tip: '주의: 기관 수급 반전 신호 주시 | 연기금·투신·보험 구분 확인 추천 | 외국인과 동시 매수 시 신뢰도 ↑',
    col: 'institution_net_buy',
    makeTag: row => {
      const v = Number(row.institution_net_buy || 0);
      return { label: v >= 0 ? `기관 +${v.toLocaleString()}` : `기관 ${v.toLocaleString()}`, cls: v >= 0 ? 'ev-blue' : 'ev-red' };
    },
  },
  liquidity_filter: {
    label: '유동성 필터',
    desc: '일평균 거래대금 기준 미달 종목을 제거했습니다. 매수·매도 집행 시 시장충격 비용이 낮고 원하는 가격에 체결될 가능성이 높습니다.',
    tip: '단기매매: 일 거래대금 50억 이상 권장 | 스윙: 10억 이상 | 급등 후 유동성 급감 종목 주의',
    col: null,
    makeTag: () => ({ label: '유동성 ✓', cls: 'ev-green' }),
  },
  rs_rating: {
    label: '상대 강도(RS Rating)',
    desc: '지수 대비 수익률 백분위입니다. 80점 이상은 시장 주도주군에 속함을 의미합니다.',
    tip: 'RS 90 이상: 시장 최상위 주도주 | 80 이상: 주도 섹터 핵심주',
    col: 'rs_rating',
    makeTag: row => ({ label: `RS ${Math.round(row.rs_rating || 0)}`, cls: 'ev-accent' }),
  },
  sector: {
    label: '섹터 분류',
    desc: 'KRX 업종 분류 및 수동 매핑을 통해 종목의 산업군을 표시합니다.',
    tip: '',
    col: 'sector',
    makeTag: row => ({ label: row.sector || '기타', cls: 'ev-purple' }),
  },
  macro_filter: {
    label: '매크로 분석',
    desc: 'VIX 지수 및 S&P500/KOSPI 200일선 추세를 분석하여 시장 위험도를 평가합니다.',
    tip: 'VIX 30 이상: 위험 관리 모드 | 지수 200일선 아래: 비중 축소',
    col: 'macro_vix',
    makeTag: row => ({ label: row.macro_vix > 30 ? '🚨 위험' : '매크로 ✓', cls: row.macro_vix > 30 ? 'ev-red' : 'ev-green' }),
  },
  score_filter: {
    label: '최종 점수 종합',
    desc: 'VCP, 돌파, RS, 수급 점수를 합산하여 Tier 1/2/3 등급을 부여합니다.',
    tip: 'Tier 1: 핵심 매수 후보 | Tier 2: 관심 종목 | Tier 3: 관찰 필요',
    col: 'total_score',
    makeTag: row => ({ label: `Tier ${row.tier || 3} (${row.total_score || 0}점)`, cls: row.tier === 1 ? 'ev-accent' : 'ev-purple' }),
  },
};

const COMBO_HINTS = [
  { need: ['vcp', 'foreign_flow'], text: '외국인 매집 중인 VCP 패턴 — 기술적 셋업과 수급이 동시에 갖춰진 가장 강력한 추세 추종 조합입니다.' },
  { need: ['box_breakout', 'ma_alignment'], text: '이평선 정배열 + 박스권 돌파 — 추세 환경이 갖춰진 상태에서의 돌파로 지속 가능성이 높습니다.' },
  { need: ['foreign_flow', 'institution_flow'], text: '외국인·기관 동시 순매수 — 수급 양쪽에서 매집 중. 매도 압력이 약할 때 강한 상승이 나타날 수 있습니다.' },
  { need: ['vcp', 'box_breakout'], text: 'VCP + 박스권 돌파 — 눌림목 수축 후 고점 돌파. 변동성 확장 직전 신호입니다.' },
  { need: ['vcp', 'foreign_flow', 'institution_flow'], text: '외국인·기관 동시 매집 + VCP — 세 조건이 동시에 맞는 종목은 매우 희귀하며 강한 상승 가능성이 있습니다.' },
];

// ── 상태 ──
let state = {
  market: 'ALL',
  enabledSignals: new Set(['vcp', 'foreign_flow']),
  combine: 'and',
  sortColumn: 'market_cap',
  sortAsc: false,
  topN: 50,
  signalParams: {},
  results: null,
  aiProvider: 'gemini',
  aiComments: {},
};

// ── Init ──
document.addEventListener('DOMContentLoaded', () => {
  renderSignals();
  setupMarket();
  setupCombine();
  setupSort();
  setupRun();
  setupMode();
  setupAiSettings();
  SIGNALS.forEach(s => {
    state.signalParams[s.id] = {};
    s.params.forEach(p => { state.signalParams[s.id][p.key] = p.default; });
  });
});

// ── Signal Checkboxes ──
function renderSignals() {
  const container = document.getElementById('signalList');
  container.innerHTML = SIGNALS.map(s => {
    const checked = state.enabledSignals.has(s.id) ? 'checked' : '';
    const active = checked ? 'active' : '';
    const paramsHtml = s.params.map(p => `
      <div class="param-inline">
        <label>${p.label}</label>
        <input type="number" data-signal="${s.id}" data-param="${p.key}"
               value="${p.default}" step="${p.step || 1}" min="0">
      </div>
    `).join('');
    return `
      <div class="signal-item ${active}" data-id="${s.id}">
        <div class="signal-row">
          <input type="checkbox" class="signal-check" data-id="${s.id}" ${checked}>
          <div class="signal-info">
            <div class="signal-name">${s.name}</div>
            <div class="signal-desc">${s.desc}</div>
          </div>
          <span class="signal-tag ${s.tag}">${s.tag}</span>
        </div>
        ${paramsHtml ? `<div class="signal-params">${paramsHtml}</div>` : ''}
      </div>
    `;
  }).join('');

  container.querySelectorAll('.signal-check').forEach(cb => {
    cb.addEventListener('change', e => {
      const id = e.target.dataset.id;
      if (e.target.checked) state.enabledSignals.add(id);
      else state.enabledSignals.delete(id);
      e.target.closest('.signal-item').classList.toggle('active', e.target.checked);
    });
  });
  container.querySelectorAll('.param-inline input').forEach(inp => {
    inp.addEventListener('change', e => {
      state.signalParams[e.target.dataset.signal][e.target.dataset.param] = parseFloat(e.target.value) || 0;
    });
  });

  const btnSelectAll = document.getElementById('btnSelectAllSignals');
  if (btnSelectAll) {
    btnSelectAll.addEventListener('click', () => {
      const allChecked = Array.from(container.querySelectorAll('.signal-check')).every(cb => cb.checked);
      container.querySelectorAll('.signal-check').forEach(cb => {
        cb.checked = !allChecked;
        const id = cb.dataset.id;
        if (!allChecked) state.enabledSignals.add(id);
        else state.enabledSignals.delete(id);
        cb.closest('.signal-item').classList.toggle('active', !allChecked);
      });
      btnSelectAll.textContent = allChecked ? '전체 선택' : '전체 해제';
    });
  }
}


// ── Market ──
function setupMarket() {
  document.querySelectorAll('.market-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      document.querySelectorAll('.market-chip').forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      state.market = chip.dataset.market;
    });
  });
}

// ── Combine ──
function setupCombine() {
  document.querySelectorAll('.combine-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.combine-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.combine = btn.dataset.combine;
    });
  });
}

// ── Sort ──
function setupSort() {
  const sortSel = document.getElementById('sortColumn');
  const topNInp = document.getElementById('topN');
  if (sortSel) sortSel.addEventListener('change', e => { state.sortColumn = e.target.value; });
  if (topNInp) topNInp.addEventListener('change', e => { state.topN = parseInt(e.target.value) || 50; });
}

// ── Mode switch ──
function setupMode() {
  document.getElementById('modeDag')?.addEventListener('click', () => { window.location.href = '/dag'; });
}

// ── Step 2 & 3: AI 설정 ──────────────────────────────────────────────────────
function setupAiSettings() {
  const savedKey = localStorage.getItem('af_api_key') || '';
  const savedProvider = localStorage.getItem('af_provider') || 'gemini';

  const keyInput = document.getElementById('aiApiKey');
  if (keyInput && savedKey) keyInput.placeholder = '저장됨 (변경하려면 입력)';

  state.aiProvider = savedProvider;
  document.querySelectorAll('.provider-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.provider === savedProvider);
  });
  updateAiHint(savedProvider);
  updateAiStatus(savedKey);

  document.querySelectorAll('.provider-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.provider-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.aiProvider = btn.dataset.provider;
      localStorage.setItem('af_provider', state.aiProvider);
      updateAiHint(state.aiProvider);
    });
  });

  document.getElementById('btnSaveKey')?.addEventListener('click', saveApiKey);
  document.getElementById('aiApiKey')?.addEventListener('keydown', e => { if (e.key === 'Enter') saveApiKey(); });
}

function saveApiKey() {
  const input = document.getElementById('aiApiKey');
  if (!input) return;
  const key = input.value.trim();
  if (key) {
    localStorage.setItem('af_api_key', key);
    updateAiStatus(key);
    input.value = '';
    input.placeholder = '저장됨 (변경하려면 입력)';
    showToast('API 키가 저장되었습니다.');
  } else {
    localStorage.removeItem('af_api_key');
    updateAiStatus('');
    showToast('API 키가 삭제되었습니다.');
  }
}

function getApiKey() { return localStorage.getItem('af_api_key') || ''; }

function updateAiHint(provider) {
  const hints = {
    gemini: 'Google AI Studio에서 무료 Gemini API 키를 발급받으세요. 하루 20회 무료 (10종목 배치 분석).',
    claude: 'Anthropic Console에서 Claude API 키를 발급받으세요. 유료 (약 $0.001~0.003/종목).',
  };
  const el = document.getElementById('aiHint');
  if (el) el.textContent = hints[provider] || '';
}

function updateAiStatus(key) {
  const el = document.getElementById('aiStatus');
  if (!el) return;
  el.textContent = key ? '연결됨' : '미설정';
  el.className = 'ai-badge ' + (key ? 'connected' : 'disconnected');
}

function showToast(msg) {
  let toast = document.getElementById('afToast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'afToast';
    toast.className = 'af-toast';
    document.body.appendChild(toast);
  }
  toast.textContent = msg;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 2200);
}

// ── RUN ──
function setupRun() {
  document.getElementById('btnRun')?.addEventListener('click', runStrategy);
  document.getElementById('btnSingleAnalyze')?.addEventListener('click', runSingleAnalyze);
  document.getElementById('targetStock')?.addEventListener('keydown', e => { if (e.key === 'Enter') runSingleAnalyze(); });
}

async function runSingleAnalyze() {
  const btn = document.getElementById('btnSingleAnalyze');
  const input = document.getElementById('targetStock');
  const query = input.value.trim();
  const loading = document.getElementById('loadingOverlay');
  const enabled = Array.from(state.enabledSignals);

  if (!query) { alert('분석할 종목명이나 코드를 입력하세요.'); return; }
  if (enabled.length === 0) { alert('시그널을 1개 이상 선택하세요.'); return; }

  btn.disabled = true;
  loading.classList.add('show');
  state.aiComments = {};

  try {
    const t0 = performance.now();
    const resp = await fetch('/api/analyze_single_stock', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, dag_config: buildDAG(enabled) }),
    });
    const result = await resp.json();
    const elapsed = Math.round(performance.now() - t0);

    if (result.error) { alert('분석 실패: ' + result.error); return; }

    state.results = result;
    renderResults(result, elapsed, enabled);
    renderStrategyInsight(enabled, state.combine);
    showToast(`${result.target_name} 정밀 분석 완료`);

    const apiKey = getApiKey();
    if (apiKey) runAiAnalysis(result, enabled, apiKey);

  } catch (err) {
    alert('API 오류: ' + err.message);
  } finally {
    btn.disabled = false;
    loading.classList.remove('show');
  }
}

async function runStrategy() {
  const btn = document.getElementById('btnRun');
  const loading = document.getElementById('loadingOverlay');
  const enabled = Array.from(state.enabledSignals);

  if (enabled.length === 0) { alert('시그널을 1개 이상 선택하세요.'); return; }

  btn.textContent = '⏳ 분석 중...';
  btn.classList.add('running');
  loading.classList.add('show');
  state.aiComments = {};

  try {
    const t0 = performance.now();
    const resp = await fetch('/api/execute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildDAG(enabled)),
    });
    const result = await resp.json();
    const elapsed = Math.round(performance.now() - t0);

    if (!result.success) { alert('실행 실패: ' + (result.error || '')); return; }

    state.results = result;
    renderResults(result, elapsed, enabled);
    renderStrategyInsight(enabled, state.combine);

    const apiKey = getApiKey();
    if (apiKey) runAiAnalysis(result, enabled, apiKey);

  } catch (err) {
    alert('API 오류: ' + err.message);
  } finally {
    btn.textContent = '▶ 분석 실행';
    btn.classList.remove('running');
    loading.classList.remove('show');
  }
}

// ── DAG 자동 조립 ──
// 신호를 역할별로 분류하여 순서 보장:
//   PRE_CHAIN    : 유니버스 직후 전처리 (순차)
//   FILTER_POOL  : 기술적 필터 (AND=순차, OR=병렬→or_filter)
//   ENRICH_CHAIN : 필터 통과 후 컬럼 추가 (순차, 컬럼 손실 없음)
//   POST_CHAIN   : 최종 점수·섹터·매크로 (순차)
function buildDAG(enabledIds) {
  const PRE_CHAIN    = ['liquidity_filter'];
  const FILTER_POOL  = ['vcp', 'box_breakout', 'ma_alignment'];
  const ENRICH_CHAIN = ['foreign_flow', 'institution_flow', 'rs_rating'];
  const POST_CHAIN   = ['sector', 'macro_filter', 'score_filter'];

  const nodes = [], edges = [];
  let seq = 1;
  const nid = () => `n${seq++}`;

  const addSigNode = (sigId) => {
    const sig = SIGNALS.find(s => s.id === sigId);
    if (!sig) return null;
    const nodeId = nid();
    let params = { ...(state.signalParams[sigId] || {}), ...(sig.fixedParams || {}) };
    if (sig.transformParams) params = { ...params, ...sig.transformParams(state.signalParams[sigId] || {}) };
    nodes.push({ id: nodeId, type: sigId, params });
    return nodeId;
  };
  const link = (from, to) => edges.push({ from, to });

  // 1. Universe
  const univId = nid();
  nodes.push({ id: univId, type: 'universe', params: { market: state.market } });
  let tail = univId;

  // 2. Pre-chain (순차)
  for (const id of PRE_CHAIN) {
    if (!enabledIds.includes(id)) continue;
    const n = addSigNode(id);
    if (n) { link(tail, n); tail = n; }
  }

  // 3. Filter signals
  const activeFilters = FILTER_POOL.filter(id => enabledIds.includes(id));
  if (activeFilters.length === 1) {
    const n = addSigNode(activeFilters[0]);
    if (n) { link(tail, n); tail = n; }
  } else if (activeFilters.length >= 2) {
    if (state.combine === 'or') {
      // OR: 병렬 → or_filter (합집합)
      const fids = activeFilters.map(id => { const n = addSigNode(id); link(tail, n); return n; }).filter(Boolean);
      const orId = nid();
      nodes.push({ id: orId, type: 'or_filter', params: {} });
      fids.forEach(f => link(f, orId));
      tail = orId;
    } else {
      // AND: 순차 체인 (자연스러운 교집합, 컬럼 누적 보장)
      for (const id of activeFilters) {
        const n = addSigNode(id);
        if (n) { link(tail, n); tail = n; }
      }
    }
  }

  // 4. Enrich chain (항상 순차 — 컬럼 누적이 핵심)
  for (const id of ENRICH_CHAIN) {
    if (!enabledIds.includes(id)) continue;
    const n = addSigNode(id);
    if (n) { link(tail, n); tail = n; }
  }

  // 5. Post chain (항상 순차)
  for (const id of POST_CHAIN) {
    if (!enabledIds.includes(id)) continue;
    const n = addSigNode(id);
    if (n) { link(tail, n); tail = n; }
  }

  // 6. Top N
  const topNId = nid();
  nodes.push({ id: topNId, type: 'top_n', params: { sort_column: state.sortColumn, ascending: state.sortAsc, n: state.topN } });
  link(tail, topNId);

  return { nodes, edges };
}

// ── Step 1: 결과 렌더링 (근거 태그 포함) ──
function renderResults(result, elapsedMs, enabledIds) {
  const nr = result.node_results;
  const nodeIds = Object.keys(nr);
  const univNode = Object.values(nr).find(n => n.total_count > 1000);
  const lastNode = nr[nodeIds[nodeIds.length - 1]];
  const totalUniverse = univNode ? univNode.total_count : 0;
  const totalResult = lastNode ? lastNode.total_count : 0;
  const filterRate = totalUniverse ? ((1 - totalResult / totalUniverse) * 100).toFixed(1) : 0;

  document.getElementById('statTotal').textContent = totalUniverse.toLocaleString();
  document.getElementById('statFiltered').textContent = totalResult.toLocaleString();
  document.getElementById('statRate').textContent = filterRate + '%';
  document.getElementById('statLatency').textContent = elapsedMs + 'ms';

  const data = lastNode?.data || [];
  document.getElementById('resultCount').textContent = `${data.length}개 / ${totalResult}개`;

  const resultDiv = document.getElementById('resultBody');
  if (data.length === 0) {
    resultDiv.innerHTML = `<div style="text-align:center;padding:60px 40px;color:var(--text-3);">조건에 맞는 종목이 없습니다. 필터를 조정해보세요.</div>`;
    return;
  }

  const hasKey = !!getApiKey();
  resultDiv.innerHTML = data.map((row, i) => {
    const mktClass = (row.market || '').toLowerCase();
    const close = row.close ? Number(row.close).toLocaleString() : '-';
    const vol   = row.volume ? Number(row.volume).toLocaleString() : '-';
    const cap   = row.market_cap ? formatCap(row.market_cap) : '-';
    const evHtml = buildEvidenceTags(row, enabledIds);

    const totalScore = row.total_score ?? '-';
    const tier = row.tier;
    const tierCls = tier === 1 ? 'tier-1' : tier === 2 ? 'tier-2' : 'tier-3';
    const tierBadge = tier != null ? `<span class="tier-badge ${tierCls}">Tier ${tier}</span>` : '';

    const vcpScore  = row.vcp_score != null ? Math.round(row.vcp_score) : '-';
    const bGrade    = row.box_breakout_grade || '-';
    const bGradeCls = bGrade === 'A' ? 'bg-a' : bGrade === 'B' ? 'bg-b' : bGrade === 'C' ? 'bg-c' : 'bg-d';
    const rsRating  = row.rs_rating != null ? Math.round(row.rs_rating) : '-';
    const flowScore = row.flow_score != null ? row.flow_score : '-';

    const vixVal  = row.macro_vix != null ? Number(row.macro_vix).toFixed(1) : null;
    const vixHtml = vixVal != null ? `<span class="sc-vix-tag">VIX ${vixVal}</span>` : '';
    const warnHtml = row.macro_warning ? `<div class="sc-warning">${row.macro_warning}</div>` : '';
    const sector = row.sector ? `<span>섹터 <b>${row.sector}</b></span>` : '';

    const aiHtml = hasKey
      ? `<div class="sc-ai" id="ai-${row.code}"><span class="ai-loading">분석 중…</span></div>`
      : `<div class="sc-ai" id="ai-${row.code}"><span class="ai-nokey">─</span></div>`;

    return `
      <div class="sc">
        <div class="sc-head">
          <span class="sc-rank">${i + 1}</span>
          <span class="sc-name">${row.name || ''}</span>
          <span class="sc-code">${row.code || ''}</span>
          <span class="market-badge ${mktClass}">${row.market || ''}</span>
          <span class="sc-total">${totalScore}<small>/210</small></span>
          ${tierBadge}
        </div>
        <div class="sc-meta">
          <span>종가 <b>${close}</b></span>
          <span>거래량 <b>${vol}</b></span>
          <span>시총 <b>${cap}</b></span>
          ${sector}
        </div>
        <div class="sc-scores">
          <div class="sc-score-item"><span class="sc-score-lbl">VCP</span><span class="sc-score-val">${vcpScore}</span></div>
          <div class="sc-score-item"><span class="sc-score-lbl">돌파</span><span class="sc-score-val ${bGradeCls}">${bGrade}</span></div>
          <div class="sc-score-item"><span class="sc-score-lbl">RS</span><span class="sc-score-val">${rsRating}</span></div>
          <div class="sc-score-item"><span class="sc-score-lbl">수급</span><span class="sc-score-val">${flowScore}</span></div>
          ${vixHtml}
        </div>
        <div class="sc-evidence">${evHtml}</div>
        ${warnHtml}
        ${aiHtml}
      </div>
    `;
  }).join('');
}

// ── Step 1: 근거 태그 빌더 ──
function buildEvidenceTags(row, enabledIds) {
  const tags = [];
  for (const sigId of enabledIds) {
    const meta = SIGNAL_META[sigId];
    if (!meta || !meta.makeTag) continue;
    if (meta.col !== null && !(meta.col in row)) continue;
    const { label, cls } = meta.makeTag(row);
    tags.push(`<span class="ev-tag ${cls}">${label}</span>`);
  }
  return tags.join('');
}

// ── Step 1: 전략 해설 카드 ──
function renderStrategyInsight(enabledIds, combine) {
  const card = document.getElementById('insightCard');
  if (!card || enabledIds.length === 0) return;

  const active = enabledIds.map(id => SIGNAL_META[id]).filter(Boolean);
  const combineLabel = combine === 'or' ? 'OR — 하나라도 만족' : 'AND — 모두 만족';

  const sigHtml = active.map(m => `
    <div class="insight-sig">
      <div class="insight-sig-label">${m.label}</div>
      <div class="insight-sig-desc">${m.desc}</div>
      ${m.tip ? `<div class="insight-sig-tip">📌 ${m.tip}</div>` : ''}
    </div>
  `).join('');

  const matched = COMBO_HINTS.filter(c => c.need.every(s => enabledIds.includes(s)));
  const comboHtml = matched.length
    ? `<div class="insight-combo">${matched.map(c => `<div class="insight-combo-item">⚡ ${c.text}</div>`).join('')}</div>`
    : '';

  card.innerHTML = `
    <div class="insight-header">
      <span class="insight-title">💡 전략 해설</span>
      <span class="insight-combine">${combineLabel}</span>
    </div>
    <div class="insight-sigs">${sigHtml}</div>
    ${comboHtml}
  `;
  card.style.display = 'block';
}

// ── Step 2 & 3: AI 분석 ──────────────────────────────────────────────────────
async function runAiAnalysis(result, enabledIds, apiKey) {
  const nr = result.node_results;
  const nodeIds = Object.keys(nr);
  const lastNode = nr[nodeIds[nodeIds.length - 1]];
  const stocks = lastNode?.data || [];
  if (!stocks.length) return;

  try {
    const resp = await fetch('/api/ai_comment', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stocks, api_key: apiKey, provider: state.aiProvider, signals: enabledIds }),
    });
    const data = await resp.json();

    if (data.error) {
      document.querySelectorAll('.ai-loading').forEach(el => { el.textContent = '오류'; el.style.color = 'var(--red)'; });
      showToast('AI 오류: ' + data.error.slice(0, 60));
      return;
    }

    state.aiComments = data.comments || {};
    renderAiComments(state.aiComments);
    const costMsg = data.cost_usd > 0 ? ` ($${data.cost_usd.toFixed(4)})` : '';
    showToast('AI 분석 완료' + costMsg);

  } catch (err) {
    document.querySelectorAll('.ai-loading').forEach(el => { el.textContent = '─'; });
    console.error('AI fetch 오류:', err);
  }
}

function renderAiComments(comments) {
  const gradeMap = { A: 'grade-a', B: 'grade-b', C: 'grade-c' };
  for (const [code, info] of Object.entries(comments)) {
    const el = document.getElementById(`ai-${code}`);
    if (!el) continue;
    const grade = info.grade || '─';
    const cls = gradeMap[grade] || 'grade-none';
    const reasons = (info.key_reasons || []).slice(0, 3).join(' · ');
    const snippet = reasons || (info.comment || '').slice(0, 80);
    const tooltip = (info.comment || '').replace(/"/g, '&quot;');
    el.innerHTML = `
      <div class="ai-result" title="${tooltip}">
        <span class="ai-grade ${cls}">${grade}</span>
        <span class="ai-reasons">${snippet}</span>
      </div>
    `;
  }
}

// ── 유틸 ──
function formatCap(val) {
  if (val >= 1e12) return (val / 1e12).toFixed(1) + '조';
  if (val >= 1e8)  return (val / 1e8).toFixed(0) + '억';
  return val.toLocaleString();
}
