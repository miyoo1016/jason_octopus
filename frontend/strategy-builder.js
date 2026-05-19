// ══════════════════════════════════════════════════════════════
// AlphaForge Strategy Builder
// ══════════════════════════════════════════════════════════════

const API_FALLBACK_BASES = [
  'http://127.0.0.1:8080',
  'http://127.0.0.1:8000',
  'http://127.0.0.1:8081',
];

const SCREENING_MODES = {
  or: {
    code: 'EXPLORE_MODE',
    label: '탐색 모드',
    shortLabel: 'EXPLORE_MODE / 탐색 모드',
    description: '탐색 모드입니다. 강점이 하나라도 있는 종목을 관찰 후보로 남깁니다. 매수 후보가 아니라 감시 후보입니다.',
  },
  and: {
    code: 'STRICT_MODE',
    label: '엄격 후보 모드',
    shortLabel: 'STRICT_MODE / 엄격 후보 모드',
    description: '엄격 후보 모드입니다. 주요 조건을 모두 통과한 종목만 실전 후보로 분류합니다.',
  },
  hybrid: {
    code: 'HYBRID_MODE',
    label: '셋업 모드',
    shortLabel: 'HYBRID_MODE / 셋업 모드',
    description: '셋업 모드입니다. 구조가 살아있는 후보를 추가 확인 대상으로 분류합니다.',
  },
};

function apiBaseCandidates() {
  const bases = [];
  if (window.location.protocol === 'http:' || window.location.protocol === 'https:') {
    bases.push('');
  }
  for (const base of API_FALLBACK_BASES) {
    if (!bases.includes(base)) bases.push(base);
  }
  return bases;
}

async function requestJson(path, options = {}) {
  const { timeoutMs = 120000, ...fetchOptions } = options;
  const errors = [];
  for (const base of apiBaseCandidates()) {
    const label = base || '현재 페이지 서버';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const resp = await fetch(base + path, { ...fetchOptions, signal: controller.signal });
      const text = await resp.text();
      let data = {};
      if (text) {
        try {
          data = JSON.parse(text);
        } catch (err) {
          if (resp.ok) throw new Error('API 응답이 JSON 형식이 아닙니다.');
          data = { error: text.slice(0, 200) };
        }
      }

      if (!resp.ok) {
        let msg = data.error || `${resp.status} ${resp.statusText}`;
        if (resp.status === 500) {
          console.error("서버 내부 오류 상세:", data);
          msg = "서버 결과 직렬화 오류 가능성. 터미널 로그를 확인하세요. (" + msg + ")";
        }
        if ((resp.status === 404 || resp.status === 405) && base === '') {
          errors.push(`${label}: ${msg}`);
          continue;
        }
        const err = new Error(msg);
        err.isHttpError = true;
        err.status = resp.status;
        err.detail = data;
        throw err;
      }
      return data;
    } catch (err) {
      if (err.name === 'AbortError') {
        errors.push(`${label}: ${Math.round(timeoutMs / 1000)}초 초과`);
        continue;
      }
      if (err.isHttpError) throw err;
      errors.push(`${label}: ${err.message}`);
    } finally {
      clearTimeout(timer);
    }
  }

  throw new Error(
    '백엔드 API 서버에 연결할 수 없습니다. 터미널에서 ' +
    'venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8080 ' +
    '실행 후 http://127.0.0.1:8080 으로 다시 접속하세요.'
  );
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

const SIGNALS = [
  { id: 'vcp', name: 'VCP 패턴 찾기', desc: '변동성 수축하는 패턴 필터', tag: 'signal',
    help: '차트상 변동폭이 줄어들며 에너지가 응축된 상태를 찾습니다. [수치 가이드: 2~4회 수축 시 안정적]',
    params: [
      { key: 'lookback_days', label: '분석 기간(일)', type: 'number', default: 120, help: '보통 120일(6개월)을 봅니다. 단기 반등은 60일, 장기 매집은 250일을 설정하세요.' },
      { key: 'min_score', label: 'VCP 완성도 점수', type: 'number', default: 70, help: '• 60점 미만: 형성 중 | • 70~85점: 탄탄한 응축(권장) | • 90점+: 즉시 돌파 가능성 높음' },
    ] },
  { id: 'box_breakout', name: '박스권 돌파', desc: 'N일 고점 돌파 종목', tag: 'signal',
    help: '지루한 횡보 끝에 전고점을 뚫는 순간을 포착합니다.',
    params: [
      { key: 'box_period', label: '박스권 기간', type: 'number', default: 60, help: '• 20일: 단기 돌파 | • 60일: 중기 매물 소화(권장) | • 120일+: 대세 상승 신호' },
      { key: 'breakout_pct', label: '최소 돌파율(%)', type: 'number', default: 1.0, step: 0.1, help: '• 0%: 근접 시 미리 포착 | • 1~3%: 확실한 돌파 확인(권장)' },
      { key: 'vol_C', label: '최소 거래량 배수', type: 'number', default: 1.5, step: 0.1, help: '평소 거래량의 몇 배인가? • 1.0배: 신뢰도 낮음 | • 1.5~2배: 세력 진입 신호(권장) | • 3배+: 강력한 모멘텀' },
    ] },
  { id: 'ma_alignment', name: '이평선 정배열', desc: '5 > 20 > 60 이동평균선', tag: 'signal',
    help: '정배열은 "달리는 말"입니다. 하락 추세 종목을 거르고 오르는 추세가 확립된 종목만 남깁니다.',
    params: [] },
  { id: 'rs_rating', name: '상대 강도(RS)', desc: '지수 대비 강한 종목 필터', tag: 'signal',
    help: '시장 지수(KOSPI/KOSDAQ)보다 강하게 오르는 종목을 0~100점으로 환산합니다.',
    params: [
      { key: 'lookback_days', label: 'RS 계산 기간', type: 'number', default: 252, help: '주도주 판별엔 252일(1년)이 표준입니다. 단기 강세는 63일(3개월)을 설정하세요.' },
      { key: 'min_rating', label: '최소 RS 점수', type: 'number', default: 80, help: '• 70점 미만: 시장 소외주 | • 70~85점: 상승 후보(하락장 권장) | • 85~95점: 시장 주도주 | • 95점+: 최상위 대장주' },
    ] },
  { id: 'foreign_flow', name: '외국인 수급', desc: '외국인 순매수 및 장기 추세', tag: 'signal',
    help: '큰손인 외국인의 매집 여부를 확인합니다. 연속 매수 일수가 많을수록 신뢰도가 높습니다.',
    params: [{ key: 'n_days', label: '수급 확인 기간', type: 'number', default: 5, help: '• 1~3일: 단기 매수세 | • 5~10일: 본격적인 매집 구간(권장)' }] },
  { id: 'institution_flow', name: '기관 수급', desc: '기관 순매수', tag: 'signal',
    help: '연기금, 투신 등 기관의 뒷받침 여부를 봅니다. 외국인과 동반 매수 시 "양매수"로 강력한 상승 요인이 됩니다.',
    params: [{ key: 'n_days', label: '수급 확인 기간', type: 'number', default: 5, help: '• 5일: 기관의 최근 선호도 확인(권장)' }] },
  { id: 'sector', name: '섹터 분류', desc: '섹터 정보 매핑', tag: 'filter',
    help: '강한 섹터에서 강한 종목이 나옵니다. 종목이 속한 산업군이 현재 주도 섹터인지 함께 분석합니다.', params: [] },
  { id: 'macro_filter', name: '매크로 분석', desc: 'VIX/SP500 추세 분석', tag: 'filter',
    help: '시장이 폭락할 땐 아무리 좋은 종목도 떨어집니다. 시장 위험도(VIX)가 높으면 매수를 자제하세요.', params: [] },
  { id: 'liquidity_filter', name: '유동성 필터', desc: '거래대금 부족 종목 제거', tag: 'filter',
    help: '거래가 너무 없으면 사고 싶을 때 못 사고, 팔고 싶을 때 못 팝니다. 최소한의 안전장치입니다.',
    params: [{ key: 'min_trading_value_krw', label: '최소 거래대금(억)', type: 'number', default: 20, step: 1, help: '• 10억 미만: 위험 | • 20~50억: 스윙 적합(권장) | • 100억+: 대형주/거래 활발' }],
    transformParams: p => ({ min_trading_value_krw: (p.min_trading_value_krw || 10) * 1e8 }),
  },
  { id: 'score_filter', name: '최종 점수 종합', desc: '총점 및 Tier 산출', tag: 'filter',
    help: '모든 점수를 합산해 투자 등급을 매깁니다. • Tier 1: 무결점 대장주 | • Tier 2: 우수한 후보주 | • Tier 3: 조건부 관심주', params: [] },
];

// ── Step 1: 시그널 메타데이터 ─────────────────────────────────────────────────
const SIGNAL_META = {
  vcp: {
    label: 'VCP 패턴 (변동성 수축)',
    desc: '거래량 감소와 함께 변동폭이 줄어드는 변동성 수축 패턴입니다. 큰 상승 직전 나타나는 조용한 매집 신호로, 돌파 시 빠른 상승이 나타나는 경우가 많습니다.',
    tip: '진입: 전고점 돌파 + 거래량 급증 확인 후 | 손절: 수축 저점 하향 이탈 시 | 목표: 이전 상승폭의 1~2배',
    col: 'vcp_score',
    // [FIX] vcp_status 기반 데이터 인식 태그 — DATA_MISSING이면 ✓ 표시 금지
    makeTag: row => {
      const status = row.vcp_status;
      if (status === 'VCP_STRICT' || status === 'VCP_VALID' || status === 'VCP_CONFIRMED') return { label: 'VCP ✓', cls: 'ev-accent' };
      if (status === 'VCP_FORMING' || status === 'HIGH_CONSOLIDATION' || status === 'NEAR_SETUP' || status === 'BASE_BUILDING') return { label: 'VCP 셋업', cls: 'ev-blue' };
      if (status === 'STRONG_LEADER_NO_PIVOT') return { label: 'VCP (피벗 부재)', cls: 'ev-blue' };
      if (status === 'CONTRACTION_WARN' || status === 'VCP_WARNING' || status === 'RALLY_EXHAUSTION') return { label: 'VCP 주의', cls: 'ev-red' };
      if (status === 'REVERSE_EXPANSION') return { label: 'VCP 역수축', cls: 'ev-red' };
      if (status === 'DATA_MISSING') return { label: 'VCP 데이터 부족', cls: 'ev-red' };
      if (status === 'NO_VCP' || status === 'NOT_READY') return { label: 'VCP 미형성', cls: 'ev-red' };
      return { label: `VCP ${status || '-'}`, cls: 'ev-red' };
    },
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
    // [FIX] ma_alignment_flag 기반 데이터 인식 태그
    makeTag: row => {
      const flag = row.ma_alignment_flag;
      if (flag === 'ALIGNED') return { label: '정배열 ✓', cls: 'ev-purple' };
      if (flag === 'NOT_ALIGNED') return { label: '이평 역배열', cls: 'ev-red' };
      if (flag === 'DATA_MISSING') return { label: '이평 데이터 부족', cls: 'ev-red' };
      return { label: `이평 ${flag || '-'}`, cls: 'ev-red' };
    },
  },
  score_filter: {
    label: '최종 점수 종합',
    desc: '모모든 지표를 합산하여 5단계 분류(Tier 1/2/3, Watchlist, Rejected)를 수행합니다.',
    tip: 'Tier 1: 실전 매수 후보 | Tier 2: 주도주 후보/확인 대기 | Tier 3: 관찰 후보 | Watchlist: 추적 후보',
    col: 'total_score',
    makeTag: row => {
      const bucket = row.primary_bucket || 'WATCHLIST';
      const tierLabel = bucket === 'TIER_1' ? '실전 매수' : bucket === 'TIER_2' ? '주도주 후보' : bucket === 'TIER_3' ? '관찰' : bucket === 'WATCHLIST' ? '추적' : '제외';
      const cls = bucket === 'TIER_1' ? 'ev-accent' : (bucket === 'TIER_2' ? 'ev-purple' : (bucket === 'TIER_3' ? 'ev-green' : 'ev-red'));
      return { label: `${bucket} ${tierLabel} (${row.total_score || 0}점)`, cls };
    },
  },
  foreign_flow: {
    label: '외국인 순매수',
    desc: '외국인이 최근 N일간 누적 순매수 중입니다. 정보력 있는 스마트머니의 진입으로 해석되며, 중기 상승 모멘텀과 높은 상관관계를 보입니다.',
    tip: '주의: 환율 변동과 함께 확인 필요 | 순매수→순매도 반전 시 즉시 점검 | 연속 매수 일수도 확인',
    col: 'foreign_net_buy',
    makeTag: row => {
      if (row.foreign_net_buy == null || Number(row.foreign_net_buy) === 0) return { label: '외국인 장 마감 후 갱신', cls: 'ev-red' };
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
      if (row.institution_net_buy == null || Number(row.institution_net_buy) === 0) return { label: '기관 장 마감 후 갱신', cls: 'ev-red' };
      const v = Number(row.institution_net_buy || 0);
      return { label: v >= 0 ? `기관 +${v.toLocaleString()}` : `기관 ${v.toLocaleString()}`, cls: v >= 0 ? 'ev-blue' : 'ev-red' };
    },
  },
  liquidity_filter: {
    label: '유동성 필터',
    desc: '일평균 거래대금 기준 미달 종목을 제거했습니다. 매수·매도 집행 시 시장충격 비용이 낮고 원하는 가격에 체결될 가능성이 높습니다.',
    tip: '단기매매: 일 거래대금 50억 이상 권장 | 스윙: 10억 이상 | 급등 후 유동성 급감 종목 주의',
    col: null,
    // [FIX] liquidity_status 기반 데이터 인식 태그 — Risk Gate와 표시 일치 (✓ 충돌 해소)
    makeTag: row => {
      const status = row.liquidity_status;
      if (status === 'LIQUID') return { label: '유동성 ✓', cls: 'ev-green' };
      if (status === 'LIQUIDITY_FALLBACK' || status === 'LIQUIDITY_UNVERIFIED')
        return { label: '유동성 (폴백)', cls: 'ev-blue' };
      if (status === 'LIQUIDITY_UNCERTAIN') return { label: '유동성 불확실', cls: 'ev-red' };
      if (status === 'DATA_MISSING' || status === 'LIQUIDITY_UNKNOWN' || status === 'DATA_INSUFFICIENT')
        return { label: '유동성 데이터 부족', cls: 'ev-red' };
      if (status === 'ILLIQUID') return { label: '유동성 부족', cls: 'ev-red' };
      return { label: `유동성 ${status || '?'}`, cls: 'ev-red' };
    },
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
    desc: '모든 지표를 합산하여 Tier 1/2/3, Watchlist, Rejected를 분류합니다.',
    tip: 'Tier 1: 실전 매수 후보 | Tier 2: 강한 주도주 후보 | Tier 3: 관찰 후보 | Watchlist: Risk Watch / 추적 후보',
    col: 'total_score',
    makeTag: row => {
      const bucket = row.primary_bucket || 'WATCHLIST';
      const labels = { TIER_1: '실전 매수', TIER_2: '주도주 후보', TIER_3: '관찰 후보', WATCHLIST: '추적 후보', REJECTED: '제외' };
      const clsMap = { TIER_1: 'ev-accent', TIER_2: 'ev-purple', TIER_3: 'ev-green', WATCHLIST: 'ev-blue', REJECTED: 'ev-red' };
      return { label: `${labels[bucket] || bucket} (${row.total_score || 0}점)`, cls: clsMap[bucket] || 'ev-red' };
    },
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
  maxSymbols: 30,
  signalParams: {},
  results: null,
  aiProvider: 'gemini',
  aiComments: {},
  capFilter: 'all',   // 'all' | 'large'(≥1조) | 'small'(<1조)
};

// ── Init ──
document.addEventListener('DOMContentLoaded', () => {
  renderSignals();
  setupMarket();
  setupCombine();
  setupSort();
  setupDebugLimit();
  setupCapFilter();
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
        <div class="param-tip-text">${p.help || ''}</div>
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
        <div class="signal-main-help">${s.help || ''}</div>
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

function setLoadingText(text) {
  const el = document.querySelector('#loadingOverlay .loading-text');
  if (el) el.textContent = text;
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

function setupDebugLimit() {
  const sel = document.getElementById('maxSymbols');
  if (!sel) return;
  sel.addEventListener('change', e => {
    const value = parseInt(e.target.value, 10);
    state.maxSymbols = Number.isFinite(value) ? value : 0;
  });
}

// ── Cap Filter (시총 구간) ──
function setupCapFilter() {
  document.getElementById('capFilterGroup')?.addEventListener('click', e => {
    const btn = e.target.closest('.cap-btn');
    if (!btn) return;
    document.querySelectorAll('.cap-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.capFilter = btn.dataset.cap;
    if (state.results) renderResults(state.results, null, Array.from(state.enabledSignals));
  });
}

// ── Mode switch ──
function setupMode() {
  document.getElementById('modeDag')?.addEventListener('click', () => { window.location.href = '/dag'; });
}

// ── Step 2 & 3: AI 설정 ──────────────────────────────────────────────────────
function setupAiSettings() {
  const savedKey = localStorage.getItem('af_api_key') || '';
  const savedProviderRaw = localStorage.getItem('af_provider') || 'gemini';
  const savedProvider = savedProviderRaw === 'gemini' ? 'gemini' : 'gemini';
  if (savedProviderRaw !== savedProvider) localStorage.setItem('af_provider', savedProvider);

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
      if (btn.disabled || btn.dataset.provider !== 'gemini') {
        showToast('AlphaForge v1.2 정책상 Gemini Flash만 사용합니다.');
        return;
      }
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
    claude: 'AlphaForge v1.2 정책상 유료 API 제공자는 비활성화되어 있습니다.',
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

function scrollToCode(code) {
  const el = document.querySelector(`.sc[data-code="${code}"]`);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
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
    const result = await requestJson('/api/analyze_single_stock', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, dag_config: buildDAG(enabled) }),
      timeoutMs: 120000,
    });
    const elapsed = Math.round(performance.now() - t0);

    if (result.error) { alert('분석 실패: ' + result.error); return; }

    state.results = result;
    try {
      renderResults(result, elapsed, enabled);
      renderStrategyInsight(enabled, state.combine);
    } catch (renderErr) {
      console.error("결과 렌더링 오류:", renderErr);
      alert("결과 렌더링 중 오류가 발생했습니다. 개발자 도구(F12)를 확인해 주세요.\n" + renderErr.message);
    }
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
    setLoadingText('분석 작업을 시작하는 중...');
    const job = await requestJson('/api/analysis/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...buildDAG(enabled), max_symbols: state.maxSymbols }),
      timeoutMs: 15000,
    });

    const result = await waitForAnalysisJob(job.job_id);
    const elapsed = Math.round(performance.now() - t0);

    if (!result.success) { alert('실행 실패: ' + (result.error || '')); return; }

    state.results = result;
    try {
      renderResults(result, elapsed, enabled);
      renderStrategyInsight(enabled, state.combine);
    } catch (renderErr) {
      console.error("결과 렌더링 오류:", renderErr);
      alert("결과 렌더링 중 오류가 발생했습니다. 개발자 도구(F12)를 확인해 주세요.\n" + renderErr.message);
    }

    const apiKey = getApiKey();
    if (apiKey) runAiAnalysis(result, enabled, apiKey);

  } catch (err) {
    alert('API 오류: ' + err.message);
  } finally {
    btn.textContent = '▶ 분석 실행';
    btn.classList.remove('running');
    loading.classList.remove('show');
    setLoadingText('KRX 데이터 분석 중...');
  }
}

async function waitForAnalysisJob(jobId) {
  if (!jobId) throw new Error('분석 작업 ID를 받지 못했습니다.');
  const deadline = Date.now() + 120000;
  let lastStatus = null;

  while (Date.now() < deadline) {
    lastStatus = await requestJson(`/api/analysis/jobs/${jobId}`, { timeoutMs: 10000 });
    const pct = Math.round((lastStatus.progress || 0) * 100);
    const node = lastStatus.current_node_type || lastStatus.current_node || '대기';
    setLoadingText(`데이터 분석 중... ${pct}% (${node})`);

    if (lastStatus.status === 'completed') {
      return await requestJson(`/api/analysis/jobs/${jobId}/result`, { timeoutMs: 30000 });
    }
    if (lastStatus.status === 'failed') {
      throw new Error(lastStatus.error || '분석 작업이 실패했습니다.');
    }
    await sleep(1500);
  }

  throw new Error('분석이 120초를 초과했습니다. 빠른 진단은 분석 종목 수를 30개로 낮춰 다시 실행하세요.');
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
    if (sigId === 'score_filter') {
      const liquidityParams = state.signalParams.liquidity_filter || {};
      params = {
        ...params,
        screening_mode: (SCREENING_MODES[state.combine] || SCREENING_MODES.and).code,
        min_trading_value_krw: (Number(liquidityParams.min_trading_value_krw) || 20) * 1e8,
      };
    }
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
  const nr = result.node_results || {};
  const nodeIds = Object.keys(nr);
  const univNode = Object.values(nr).find(n => n.node_type === 'universe') || Object.values(nr)[0];
  const lastNode = nr[nodeIds[nodeIds.length - 1]];
  const structuredRows = collectStructuredRows(result);
  const totalUniverse = safeNumber(result.summary?.universe_count);
  const totalClassified = safeNumber(result.summary?.primary_total_count);
  const totalFiltered = safeNumber(result.summary?.filtered_count);
  const coreCandidateCount = safeNumber(result.summary?.core_candidate_count);
  const preFilterRate = safePercent(totalFiltered, totalUniverse);

  document.getElementById('statTotal').textContent = totalUniverse.toLocaleString();
  document.getElementById('statFiltered').textContent = totalClassified.toLocaleString();
  document.getElementById('statRate').textContent = preFilterRate + '%';
  document.getElementById('statLatency').textContent = elapsedMs + 'ms';

  if (result.summary && result.summary.performance_summary) {
    renderPerformance(result.summary.performance_summary);
  }
  if (result.summary && result.summary.operation_report) {
    renderOperationReport(result.summary.operation_report);
  }
  const allData = structuredRows.length ? structuredRows : (lastNode?.data || []);

  // ── 시총 구간 필터 적용 ─────────────────────────────────────────
  const CAP_1T = 1_000_000_000_000; // 1조
  const data = allData.filter(row => {
    const cap = row.market_cap || 0;
    if (state.capFilter === 'large') return cap >= CAP_1T;
    if (state.capFilter === 'small') return cap < CAP_1T;
    return true;
  });

  const capLabel = state.capFilter === 'large' ? ' (대형주)' : state.capFilter === 'small' ? ' (중소형주)' : '';
  document.getElementById('resultCount').textContent = `${data.length}개${capLabel} / 전체 ${totalClassified}개 분류`;

  const resultDiv = document.getElementById('resultBody');
  if (data.length === 0) {
    resultDiv.innerHTML = `
      <div style="text-align:center;padding:48px 40px;color:var(--text-3);">
        <b style="display:block;color:var(--text-2);margin-bottom:8px;">현재 조건을 모두 만족한 종목은 없습니다.</b>
        다만 아래 Watchlist / Near Setup / RS Leader 후보와 진단 정보를 참고하세요.
      </div>
      ${renderFallbackSections(result, enabledIds)}
      ${renderDiagnosticsPanel(result)}
    `;
    return;
  }

  // ── 섹터별 RS 1위 블록 ─────────────────────────────────────────
  const sectorRsBlock = buildSectorRsBlock(allData);

  const hasKey = !!getApiKey();
  resultDiv.innerHTML = renderResultSummaryStrip(result) + renderDiagnosticsPanel(result) + sectorRsBlock + data.map((row, i) => {
    const mktClass = (row.market || '').toLowerCase();
    const close = row.close ? Number(row.close).toLocaleString() : '-';
    const vol   = row.volume ? Number(row.volume).toLocaleString() : '-';
    const cap   = row.market_cap ? formatCap(row.market_cap) : '-';
    const evHtml = buildEvidenceTags(row, enabledIds);

    const { totalScore, scoreMax } = getScoreInfo(row, result);
    const noFlowBadge = row.has_flow === false ? `<span class="tier-badge" style="background:#f3f4f6;color:#6b7280;font-size:9px;">수급 미반영</span>` : '';
    const bucketBadge = row._bucket ? `<span class="tier-badge" style="background:#eef2ff;color:#4338ca;">${row._bucket}</span>` : '';

    const tier = row.tier;
    const bucket = row.primary_bucket || 'WATCHLIST';
    const tierCls = bucket === 'TIER_1' ? 'tier-1' : bucket === 'TIER_2' ? 'tier-2' : bucket === 'TIER_3' ? 'tier-3' : 'watchlist';
    const tierReason = row.tier_reason || '';

    let bucketDisplay = bucket;
    if (bucket === 'TIER_1') bucketDisplay = 'Tier 1 실전 매수';
    else if (bucket === 'TIER_2') bucketDisplay = 'Tier 2 주도주 후보';
    else if (bucket === 'TIER_3') bucketDisplay = 'Tier 3 관찰 후보';
    else if (bucket === 'WATCHLIST') bucketDisplay = 'Watchlist 추적';
    else if (bucket === 'CRISIS_HOLD') bucketDisplay = 'Crisis Hold 보류';
    else if (bucket === 'REJECTED') bucketDisplay = 'Rejected 제외';

    const tierBadge = `<span class="tier-badge ${tierCls}" title="${tierReason}">${bucketDisplay}</span>`;
    // [FIX] 화면용 라벨은 legacy ACTION_ALERT를 완화해서 표시
    const alertType = getDisplayWatchAlertType(row);
    const _alertConfig = {
      'BUY_CANDIDATE': { emoji: '◎', label: 'BUY CANDIDATE', bg: '#047857' },
      'NEAR_BUY': { emoji: '○', label: 'NEAR BUY', bg: '#2563eb' },
      'PRIORITY_WATCH': { emoji: '★', label: 'PRIORITY WATCH', bg: '#7c3aed' },
      'RISK_WATCH':   { emoji: '⚠️', label: 'RISK WATCH',   bg: '#f59e0b' },
      'SETUP_WATCH':  { emoji: '◇', label: 'SETUP WATCH',  bg: '#6366f1' },
      'REJECTED': { emoji: '', label: 'REJECTED', bg: '#6b7280' },
    };
    const _alertCfg = _alertConfig[alertType];
    const alertTitle = (asArray(row.display_watch_alert_reasons).length ? asArray(row.display_watch_alert_reasons) : asArray(row.watch_alert_reasons_display)).join('; ') || alertType;
    const watchAlertBadge = (_alertCfg && (getWatchAlert(row) || ['BUY_CANDIDATE', 'NEAR_BUY', 'PRIORITY_WATCH'].includes(alertType)))
      ? `<span class="tier-badge" style="background:${_alertCfg.bg};color:white;margin-left:4px;" title="${alertTitle}">${_alertCfg.emoji} ${_alertCfg.label}</span>`
      : (row.watchlist_flag
        ? `<span class="tier-badge" style="background:var(--accent);color:white;margin-left:4px;">관찰 라벨</span>`
        : '');
    const tierReasonHtml = tierReason ? `<span class="tier-reason">${tierReason}</span>` : '';

    // 승격/관찰 사유 블록
    const _displayReasons = bucket === 'REJECTED' ? getDisplayRejectedReasons(row) : getDisplayPromotionReasons(row);
    const _isTier = bucket.startsWith('TIER');
    const _isWatchlist = bucket === 'WATCHLIST' || bucket === 'CRISIS_HOLD';
    const _reasonLabel = _isTier ? '승격 사유' : _isWatchlist ? '관찰 사유' : bucket === 'REJECTED' ? '제외 사유' : '';
    const reasonHtml = (_reasonLabel && _displayReasons.length)
      ? `<div class="sc-reasons"><span class="sc-reasons-label">${_reasonLabel}:</span> ${_displayReasons.join(' · ')}</div>`
      : '';

    // 급등 당일 경고 플래그 (+15% 이상)
    const changePct = row.change_pct != null ? Number(row.change_pct) : null;
    const surgeBadge = (changePct != null && changePct >= 0.15)
      ? `<span class="surge-warn" title="당일 +${(changePct * 100).toFixed(1)}% 급등 — 추격매수 주의">⚡ +${(changePct * 100).toFixed(1)}% 추격 주의</span>`
      : '';

    // [FIX] DATA_MISSING이면 점수 대신 N/A 표시 (50점 가산 의미 없음)
    // [FIX] VCP raw vs display 분리 — cross_warning이 있으면 raw도 함께 표시
    const _vcpDisplay = row.vcp_display_score ?? row.vcp_score;
    const _vcpRaw = row.vcp_raw_score;
    const _vcpCrossWarn = row.vcp_cross_warning;
    let vcpScore;
    if (row.vcp_status === 'DATA_MISSING' || _vcpDisplay == null) {
      vcpScore = 'N/A';
    } else if (_vcpRaw != null && _vcpCrossWarn && Math.round(_vcpRaw) !== Math.round(_vcpDisplay)) {
      // raw가 display와 다르면 둘 다 표시 (raw는 진단용)
      vcpScore = `${Math.round(_vcpDisplay)} <small style="opacity:0.6;">(raw ${Math.round(_vcpRaw)})</small>`;
    } else {
      vcpScore = Math.round(_vcpDisplay);
    }
    const bGrade    = row.box_breakout_grade || '-';
    const bGradeCls = String(bGrade).startsWith('A') ? 'bg-a' : String(bGrade).startsWith('B') ? 'bg-b' : String(bGrade).startsWith('C') ? 'bg-c' : 'bg-d';
    const breakoutStatus = row.breakout_status || row.box_breakout_flag || '-';
    const breakoutScore = (breakoutStatus === 'DATA_MISSING' || row.breakout_score == null) ? 'N/A' : Math.round(row.breakout_score);
    const rsRating  = (row.rs_status === 'DATA_MISSING' || row.rs_rating == null) ? 'N/A' : Math.round(row.rs_rating);
    const flowScoreBase = row.flow_total_score != null ? row.flow_total_score : (row.flow_score != null ? row.flow_score : '-');
    const hasPendingFlow = row.foreign_net_buy == null || row.institution_net_buy == null || Number(row.foreign_net_buy) === 0 || Number(row.institution_net_buy) === 0;
    const flowScore = hasPendingFlow && flowScoreBase !== '-' ? `${flowScoreBase} (전일 기준)` : flowScoreBase;
    const vcpComponents = row.vcp_component_scores || row.vcpComponentScores;
    const vcpComponentText = vcpComponents && typeof vcpComponents === 'object'
      ? Object.entries(vcpComponents)
          .filter(([key]) => key !== 'component_total' && key !== 'final_raw_score')
          .map(([key, val]) => `${key}:${val}`)
          .join(', ')
      : '';
    const vcpQualityReason = row.vcp_quality_reason || row.vcpQualityReason || '';
    const vcpDiag = row.vcp_diagnostic ||
      `raw ${row.vcp_raw_score ?? '-'} → effective ${row.vcp_effective_score ?? '-'} → display ${row.vcp_display_score ?? row.vcp_score ?? '-'}` +
      (row.vcp_confidence ? ` | ${row.vcp_confidence}` : '') +
      (row.vcp_cross_warning ? ` | ${asArray(row.vcp_cross_warning).join('; ') || row.vcp_cross_warning}` : '') +
      (vcpQualityReason ? ` | ${vcpQualityReason}` : '') +
      (vcpComponentText ? ` | ${vcpComponentText}` : '');
    const vcpDiagHtml = `<div class="sc-reasons"><span class="sc-reasons-label">VCP 진단:</span> ${vcpDiag}</div>`;

    const vixVal  = row.macro_vix != null ? Number(row.macro_vix).toFixed(1) : null;
    const vixHtml = vixVal != null ? `<span class="sc-vix-tag">VIX ${vixVal}</span>` : '';
    const rawScore = row.raw_score ?? row.total_score ?? '-';
    const effectiveScore = row.effective_score == null ? 'N/A' : row.effective_score;
    const gateStatus = row.gate_status || '-';
    const finalClass = row.final_class || bucket;
    const gateHtml = `<div class="sc-gate"><span>Raw <b>${rawScore}</b></span><span>Gate <b>${gateStatus}</b></span><span>Effective <b>${effectiveScore}</b></span><span>Final <b>${finalClass}</b></span></div>`;
    const failedBuyGates = asArray(row.failed_buy_gates || row.failedBuyGates);
    const buyGateHtml = row.buy_gate_passed || row.buyGatePassed
      ? `<div class="sc-reasons"><span class="sc-reasons-label">BUY 게이트:</span> PASS</div>`
      : (failedBuyGates.length || row.buy_gate_reason || row.buyGateReason)
        ? `<div class="sc-reasons"><span class="sc-reasons-label">BUY 게이트 미통과:</span> ${(failedBuyGates.length ? failedBuyGates : [row.buy_gate_reason || row.buyGateReason]).join(' · ')}</div>`
        : '';
    const warnings = [row.macro_warning, row.flow_data_warning, row.vcp_warning, row.vi_warning].filter(Boolean);
    const warnHtml = warnings.length ? `<div class="sc-warning">${warnings.join(' · ')}</div>` : '';

    // 야간/주말 데이터 불안정 경고 배너
    // VIX 미수집 + RS 0점 중 2개 이상 → 결과 신뢰 불가 경고
    const dataUnstableFlags = [
      row.macro_vix == null,
      (row.rs_rating == null || row.rs_rating === 0),
      (row.institution_net_buy == null),
    ].filter(Boolean).length;
    const unstableBanner = dataUnstableFlags >= 2
      ? `<div class="data-unstable-banner">⚠️ 데이터 불안정 — 장중(09:00~15:30) 또는 장후(18:00~) 재실행 권장. 현재 결과 참고 불가.</div>`
      : '';
    const sector = row.sector ? `<span>섹터 <b>${row.sector}</b></span>` : '';

    const aiHtml = hasKey
      ? `<div class="sc-ai" id="ai-${row.code}"><span class="ai-loading">분석 중…</span></div>`
      : `<div class="sc-ai" id="ai-${row.code}"><span class="ai-nokey">─</span></div>`;

    return `
      <div class="sc" data-code="${row.code || ''}">
        ${unstableBanner}
        <div class="sc-head">
          <span class="sc-rank">${i + 1}</span>
          <span class="sc-name">${row.name || ''}</span>
          <span class="sc-code">${row.code || ''}</span>
          <span class="market-badge ${mktClass}">${row.market || ''}</span>
          <span class="sc-total">${totalScore}<small>/${scoreMax}</small></span>
          ${noFlowBadge}
          ${tierBadge}
          ${bucketBadge}
          ${watchAlertBadge}
          ${surgeBadge}
        </div>
        ${tierReasonHtml}
        ${reasonHtml}
        ${gateHtml}
        ${buyGateHtml}
        ${vcpDiagHtml}
        <div class="sc-meta">
          <span>종가 <b>${close}</b></span>
          <span>거래량 <b>${vol}</b></span>
          <span>시총 <b>${cap}</b></span>
          ${sector}
        </div>
        <div class="sc-scores">
          <div class="sc-score-item"><span class="sc-score-lbl">VCP</span><span class="sc-score-val">${vcpScore}</span></div>
          <div class="sc-score-item"><span class="sc-score-lbl">돌파</span><span class="sc-score-val ${bGradeCls}" title="${breakoutStatus}">${breakoutScore} · ${breakoutStatus}</span></div>
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

function collectStructuredRows(result) {
  if (!result.results) return [];
  const buckets = [
    ['Tier 1', result.results.tier1 || []],
    ['Tier 2', result.results.tier2 || []],
    ['Tier 3', result.results.tier3 || []],
    ['Watchlist', result.results.watchlist || []],
    ['Crisis Hold', result.results.crisis_hold || []],
    ['Rejected', result.results.rejected || []],
  ];
  const seen = new Set();
  const rows = [];
  for (const [label, items] of buckets) {
    for (const row of items) {
      const key = row.code || `${label}-${rows.length}`;
      if (seen.has(key)) continue;
      seen.add(key);
      rows.push({ ...row, _bucket: label });
    }
  }
  return rows;
}

function renderResultSummaryStrip(result) {
  const s = result.summary || {};
  const regime = s.market_regime || result.diagnostics?.market_regime || {};

  // [Refinement] 신규 UI 라벨 및 백엔드 필드 대응
  const universe = safeNumber(s.universe_count || s.total_analyzed_count);
  const classified = safeNumber(s.primary_total_count || s.classification_completed_count);
  const core = safeNumber(s.core_candidate_count || (s.tier1_count + s.tier2_count));
  const rejected = safeNumber(s.rejected_count || s.final_rejected_count);
  const filtered = safeNumber(s.filtered_count || s.intermediate_filtered_count);
  const buyCandidateCount = safeNumber(s.buy_candidate_count || s.hard_gate_buy_candidate_count);
  const nearBuyCount = safeNumber(s.near_buy_count);

  const filterRate = s.intermediate_filtered_rate != null ? s.intermediate_filtered_rate : safePercent(filtered, universe);
  const rejectRate = s.final_rejected_rate != null ? s.final_rejected_rate : safePercent(rejected, universe);
  const alertRate = s.watch_alert_rate != null ? s.watch_alert_rate : safePercent(s.watchlist_flag_true_count, universe);
  const noBuyBanner = buyCandidateCount === 0
    ? `<div class="no-buy-banner">
        <strong>${s.no_buy_candidate_message || '오늘 실전 매수 후보 없음'}</strong>
        <span>${s.watch_only_message || '현재 결과는 관찰 후보이며 자동 매수 신호가 아닙니다.'}</span>
      </div>`
      : `<div class="buy-candidate-banner">
        <strong>${s.buy_candidate_message || '하드 게이트 통과 매수 후보'}</strong>
        <span>BUY_CANDIDATE ${buyCandidateCount}개가 모든 매수 하드 게이트를 통과했습니다.</span>
      </div>`;

  return `${noBuyBanner}<div class="diagnostics-panel" style="margin-bottom:12px;">
    <div class="diagnostics-grid" style="grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));">
      <span>전체 분석 종목 <b>${universe.toLocaleString()}</b></span>
      <span>분류 완료 <b>${classified.toLocaleString()}</b></span>
      <span>핵심 후보(T1+T2) <b>${core}</b></span>
      <span>Tier 1 <b>${safeNumber(s.tier1_count)}</b></span>
      <span>Tier 2 <b>${safeNumber(s.tier2_count)}</b></span>
      <span>BUY_CANDIDATE <b>${buyCandidateCount}</b></span>
      <span>NEAR_BUY <b>${nearBuyCount}</b></span>
      <span>Crisis Hold <b>${safeNumber(s.crisis_hold_count)}</b></span>
      <span>Priority Watch <b>${safeNumber(s.priority_watch_count)}</b></span>
      <span>Risk Watch <b>${safeNumber(s.risk_watch_count)}</b></span>
      <span>Setup Watch <b>${safeNumber(s.setup_watch_count)}</b></span>
      <span>최종 리스크 제외 <b>${rejected}</b></span>
      <span>하드 필터 제거 <b>${filtered}</b></span>
      <span>하드 필터 제거율 <b>${filterRate}%</b></span>
      <span>최종 리스크 제외율 <b>${rejectRate}%</b></span>
      <span>관찰 라벨 비율 <b>${alertRate}%</b></span>
    </div>
    <div class="diagnostics-note">탐색 모드에서는 하드 필터보다 사후 분류가 우선됩니다.</div>
  </div>
  <div class="market-regime-strip">
    <div><b>Market Regime</b> ${regime.dominant_regime || '-'} <small>보조: ${regime.secondary_regime || '-'}</small></div>
    <div class="regime-bars">
      <span>RISK_ON ${safeNumber(regime.RISK_ON)}%</span>
      <span>NEUTRAL ${safeNumber(regime.NEUTRAL)}%</span>
      <span>RISK_OFF ${safeNumber(regime.RISK_OFF)}%</span>
      <span>CRISIS ${safeNumber(regime.CRISIS)}%</span>
    </div>
    <div class="regime-meta">기준일 ${regime.as_of || '-'} · 데이터 상태 ${regime.data_status || '-'}</div>
  </div>`;
}

function renderOperationReport(report) {
  const el = document.getElementById('operationReport');
  if (!el || !report) return;

  const qs = report.quality_score ?? '-';
  const qsColor = typeof qs === 'number'
    ? (qs >= 70 ? '#22c55e' : qs >= 40 ? '#f59e0b' : '#ef4444')
    : 'var(--text-2)';

  const blockingHtml = (report.top_blocking_reasons || []).length
    ? report.top_blocking_reasons.map(b =>
        `<li><code>${b.reason}</code> <small>(${b.count}건)</small></li>`
      ).join('')
    : '<li style="color:var(--text-3)">차단 사유 없음</li>';

  el.innerHTML = `
    <div class="op-report-card" style="background:var(--bg-2,#1e1e2e);border:1px solid var(--border,#333);border-radius:10px;padding:14px 18px;margin-bottom:12px;">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px;">
        <span style="font-weight:700;font-size:14px;">🔍 운영 품질 리포트</span>
        <span style="font-size:22px;font-weight:900;color:${qsColor};">${qs}</span>
        <small style="color:var(--text-3);">/ 100 (시스템 출력 품질)</small>
        <span style="margin-left:auto;font-size:11px;padding:3px 8px;border-radius:12px;background:${report.status === 'READY' ? '#1a4731' : '#3d2c0a'};color:${report.status === 'READY' ? '#4ade80' : '#fbbf24'};">${report.status}</span>
      </div>
      <div style="font-size:13px;color:var(--text-1,#cdd6f4);background:var(--bg-3,#313244);border-radius:6px;padding:8px 12px;margin-bottom:10px;">
        ${report.operator_message || '-'}
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:10px;font-size:13px;">
        <span>🟢 매수 후보 <b>${report.buy_candidate_count ?? 0}</b></span>
        <span>🟡 NEAR_BUY <b>${report.near_buy_count ?? 0}</b></span>
        <span>🔵 Priority Watch <b>${report.priority_watch_count ?? 0}</b></span>
        <span>🔴 Risk Watch <b>${report.risk_watch_count ?? 0}</b></span>
        <span style="color:var(--text-3)">시장 국면: <b>${report.market_mode || '-'}</b></span>
      </div>
      ${(report.top_blocking_reasons || []).length ? `
      <div style="font-size:12px;">
        <span style="color:var(--text-3);font-weight:600;">주요 차단 사유 Top ${report.top_blocking_reasons.length}</span>
        <ul style="margin:4px 0 0 12px;padding:0;list-style:disc;color:var(--text-2,#cba6f7);">${blockingHtml}</ul>
      </div>` : ''}
    </div>`;
}

function renderDiagnosticsPanel(result) {
  const d = result.diagnostics;
  if (!d) return '';
  const aggressive = d.most_aggressive_filter_node;
  const nodeRows = (d.node_counts || []).map(n =>
    `<tr><td>${n.node_id}</td><td>${n.node_type}</td><td>${n.node_role || '-'}</td><td>${n.input_count}</td><td>${n.output_count}</td><td>${n.dropped_count}</td><td>${Math.round(n.elapsed_ms || 0)}ms</td><td>${Math.round((n.missing_ratio || 0) * 100)}%</td></tr>`
  ).join('');

  const safeD = (key) => asArray(d[key]).slice(0, 12).map(r => `<li>${r.reason || r} (${r.count || 1})</li>`).join('') || '<li>없음</li>';

  const promotionReasons = safeD('tier_promotion_reasons');
  const downgradeReasons = safeD('tier_downgrade_reasons');
  const rejectedReasons  = safeD('rejected_reasons');
  const riskWatchReasons = safeD('risk_watch_reasons');
  const watchReasons     = safeD('watchlist_flag_reasons');
  const watchExclusions  = safeD('watch_exclusion_reasons');
  const riskGateReasons  = safeD('risk_gate_reasons');
  const hardGateReasons  = safeD('hard_gate_reasons');
  const nanCols = (d.nan_columns || []).slice(0, 10).map(r => `<li>${r.column}: ${r.nan_count} (${Math.round((r.ratio || 0) * 100)}%)</li>`).join('') || '<li>없음</li>';
  const warnings = (d.data_quality_warnings || []).map(w => `<li>${w}</li>`).join('') || '<li>없음</li>';

  const statusBlocks = Object.entries(d.status_distributions || {}).map(([col, rows]) => {
    const items = (rows || []).slice(0, 8).map(r => `<li>${r.value}: ${r.count}</li>`).join('');
    return items ? `<div><b>${col}</b><ul>${items}</ul></div>` : '';
  }).join('');

  return `<details class="diagnostics-panel" open>
    <summary>분석 진단 및 사유 통계</summary>
    <div class="diagnostics-grid">
      <span>가장 많이 줄인 노드 <b>${aggressive ? `${aggressive.node_type} (${aggressive.dropped_count})` : '-'}</b></span>
      <span>데이터 결측 비율 <b>${Math.round((d.data_missing_ratio || 0) * 100)}%</b></span>
    </div>
    <table class="diag-table"><thead><tr><th>ID</th><th>Node</th><th>Role</th><th>Input</th><th>Output</th><th>Dropped</th><th>Time</th><th>Missing</th></tr></thead><tbody>${nodeRows}</tbody></table>
    <div class="diag-cols">
      <div><b>Tier/관찰 사유</b><ul>${promotionReasons}</ul></div>
      <div><b>Tier 제한 사유</b><ul>${downgradeReasons}</ul></div>
      <div><b>REJECTED (복합 약점 제외)</b><ul>${rejectedReasons}</ul></div>
      <div><b>WATCHLIST (Risk Watch 유지 사유)</b><ul>${riskWatchReasons}</ul></div>
      <div><b>관찰 라벨 사유</b><ul>${watchReasons}</ul></div>
      <div><b>관찰 제외 사유</b><ul>${watchExclusions}</ul></div>
      <div><b>Risk Gate 사유</b><ul>${riskGateReasons}</ul></div>
      <div><b>Hard Gate 사유</b><ul>${hardGateReasons}</ul></div>
      <div><b>NaN 핵심 컬럼</b><ul>${nanCols}</ul></div>
      <div><b>데이터 품질 경고</b><ul>${warnings}</ul></div>
      ${statusBlocks}
    </div>
  </details>`;
}

function renderFallbackSections(result, enabledIds) {
  const groups = result.results?.fallback_candidates || {};
  const labels = {
    top_score_candidates: 'Top Score Candidates',
    high_consolidation_candidates: 'High Consolidation',
    rs_leaders: 'RS Leaders',
    volume_liquidity_leaders: 'Volume/Liquidity Leaders',
    near_breakout_candidates: 'Near Setup',
  };
  return Object.entries(labels).map(([key, label]) => {
    const rows = (groups[key] || []).slice(0, 10);
    if (!rows.length) return '';
    return `<div class="fallback-section"><h3>${label}</h3><div class="fallback-list">${
      rows.map((r, i) => `<div class="fallback-row"><b>${i + 1}. ${r.name || r.code}</b><span>${r.code || ''}</span><span>Score ${r.total_score ?? r.final_score ?? '-'}</span><span>${r.breakout_status || r.vcp_status || r.rs_status || ''}</span></div>`).join('')
    }</div></div>`;
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
  const mode = SCREENING_MODES[combine] || SCREENING_MODES.and;
  const combineLabel = mode.shortLabel;

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
    <div class="insight-mode-desc">${mode.description}</div>
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
    const data = await requestJson('/api/ai_comment', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stocks, api_key: apiKey, provider: state.aiProvider, signals: enabledIds }),
    });

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
function safeNumber(val, fallback = 0) {
  if (val === null || val === undefined || isNaN(val)) return fallback;
  return Number(val);
}

function safePercent(num, den) {
  const n = safeNumber(num);
  const d = safeNumber(den);
  if (d === 0) return '0.0';
  return ((n / d) * 100).toFixed(1);
}

function getPrimaryBucket(item) {
  return item.primary_bucket || item.candidate_status || item.bucket || 'UNKNOWN';
}

function getWatchAlert(item) {
  const val = item.watchlist_flag;
  if (typeof val === 'string') return val.toLowerCase() === 'true';
  return Boolean(val);
}

function getReasonList(item, key) {
  return asArray(item[key]);
}

function asArray(value) {
  if (Array.isArray(value)) return value;
  if (value == null) return [];
  if (typeof value === "string") return value.trim() ? [value] : [];
  return [];
}

function getDisplayRejectedReasons(row) {
  const reasons = asArray(row.display_rejected_reasons);
  if (reasons.length) return reasons;
  for (const key of ['rejected_reasons', 'hard_gate_reasons', 'risk_gate_reasons', 'restriction_reasons']) {
    const fallback = asArray(row[key]);
    if (fallback.length) return fallback;
  }
  return ['세부 제외 사유 미기록'];
}

function getDisplayWatchAlertType(row) {
  const explicit = row.final_label || row.finalLabel || row.display_label || row.displayLabel || row.display_watch_alert_type || '';
  if (['BUY_CANDIDATE', 'NEAR_BUY', 'PRIORITY_WATCH', 'RISK_WATCH', 'SETUP_WATCH', 'REJECTED'].includes(explicit)) return explicit;
  if (row.buy_gate_passed || row.buyGatePassed) return 'BUY_CANDIDATE';
  const failedBuyGates = asArray(row.failed_buy_gates || row.failedBuyGates);
  if (failedBuyGates.length > 0 && failedBuyGates.length <= 2) return 'NEAR_BUY';
  const legacy = row.watch_alert_type || 'NONE';
  if (legacy === 'ACTION_ALERT') {
    const bucket = getPrimaryBucket(row);
    return bucket === 'TIER_1' || bucket === 'TIER_2' ? 'NEAR_BUY' : 'PRIORITY_WATCH';
  }
  if (legacy === 'DATA_REVIEW') return 'RISK_WATCH';
  if (legacy === 'RISK_WATCH' || legacy === 'SETUP_WATCH') return legacy;
  if (!getWatchAlert(row)) return 'NONE';
  if (row.action_alert_flag) {
    const bucket = getPrimaryBucket(row);
    return bucket === 'TIER_1' || bucket === 'TIER_2' ? 'NEAR_BUY' : 'PRIORITY_WATCH';
  }
  if (row.liquidity_status === 'LIQUIDITY_UNCERTAIN' || row.data_unit_warning_flag) return 'RISK_WATCH';
  if (['REVERSE_EXPANSION', 'RALLY_EXHAUSTION'].includes(row.vcp_status) || row.breakout_status === 'FAILED_BREAKOUT') return 'RISK_WATCH';
  return 'SETUP_WATCH';
}

function getWatchAlertText(row) {
  const type = getDisplayWatchAlertType(row);
  const text = {
    BUY_CANDIDATE: '◎ [BUY CANDIDATE]',
    NEAR_BUY: '○ [NEAR BUY]',
    PRIORITY_WATCH: '★ [PRIORITY WATCH]',
    RISK_WATCH: '⚠️ [RISK WATCH]',
    SETUP_WATCH: '◇ [SETUP WATCH]',
  };
  return getWatchAlert(row) ? (text[type] || '') : '';
}

function getPromotionReasons(row) {
  return asArray(
    row.tier_promotion_reasons ??
    row.promotion_reasons ??
    row.upgrade_reasons ??
    row.tier_reasons
  );
}

/**
 * getDisplayPromotionReasons — 개별 카드/복사용 사유 목록.
 * display_promotion_reasons 우선, 없으면 bucket 별 alias 탐색.
 * REJECTED는 항상 빈 배열 반환.
 */
function getDisplayPromotionReasons(row) {
  const bucket = (row.primary_bucket || row.candidate_status || '').toString();
  if (bucket === 'REJECTED') return [];

  // 1순위: 명시적 display field
  const display = asArray(row.display_promotion_reasons);
  if (display.length) return display;

  // 2순위: bucket 별 alias 탐색
  if (bucket.startsWith('TIER')) {
    return asArray(
      row.tier_promotion_reasons ??
      row.promotion_reasons ??
      row.upgrade_reasons ??
      row.tier_reasons ??
      row.watchlist_reasons
    );
  }
  // WATCHLIST / CRISIS_HOLD
  return asArray(
    row.watchlist_reasons ??
    row.retention_reasons ??
    row.setup_reasons ??
    row.tier_promotion_reasons ??
    row.promotion_reasons
  );
}

function getRestrictionReasons(row) {
  return asArray(
    row.tier_restriction_reasons ??
    row.restriction_reasons ??
    row.limit_reasons ??
    row.blocked_reasons
  );
}

// ── 점수 정보 유틸 ──
function getScoreInfo(item, result) {
  const totalScore = Number(item.total_score ?? item.final_score ?? item.score ?? item.totalScore ?? item.finalScore ?? 0);
  const scoreMax = Number(item.score_max ?? item.scoreMax ?? item.max_score ?? result?.summary?.score_max ?? 210);
  const safeMax = (scoreMax > 0 && !isNaN(scoreMax)) ? scoreMax : 210;
  const scorePct = Math.min(100, Math.max(0, (totalScore / safeMax) * 100)) || 0;

  return {
    totalScore: isNaN(totalScore) ? 0 : totalScore,
    scoreMax: safeMax,
    scorePct: isNaN(scorePct) ? 0 : scorePct
  };
}

function formatCap(val) {
  const v = safeNumber(val);
  if (v >= 1e12) return (v / 1e12).toFixed(1) + '조';
  if (v >= 1e8)  return (v / 1e8).toFixed(0) + '억';
  return v.toLocaleString();
}

// ── 섹터별 RS 1위 블록 생성 ──────────────────────────────────────────
function buildSectorRsBlock(data) {
  // rs_rating이 있는 종목만 대상
  const valid = data.filter(r => r.rs_rating != null && r.sector);
  if (valid.length === 0) return '';

  // 섹터별 RS 최고 종목 추출
  const sectorMap = {};
  for (const row of valid) {
    const sec = row.sector;
    if (!sectorMap[sec] || row.rs_rating > sectorMap[sec].rs_rating) {
      sectorMap[sec] = row;
    }
  }

  // RS 내림차순 정렬, 최대 12섹터
  const leaders = Object.values(sectorMap)
    .sort((a, b) => b.rs_rating - a.rs_rating)
    .slice(0, 12);

  const chips = leaders.map(row => {
    const rs   = Math.round(row.rs_rating);
    const cap  = row.market_cap ? formatCap(row.market_cap) : '';
    const tier = row.tier;
    const tierDot = tier === 1 ? '🟠' : tier === 2 ? '🔵' : '⚪';
    return `<div class="sector-rs-chip" title="${row.sector} 섹터 RS 1위" onclick="scrollToCode('${row.code}')">
      <span class="sr-sector">${row.sector}</span>
      <span class="sr-name">${tierDot} ${row.name}</span>
      <span class="sr-rs">RS ${rs}</span>
      ${cap ? `<span class="sr-cap">${cap}</span>` : ''}
    </div>`;
  }).join('');

  return `<div class="sector-rs-block">
    <div class="sector-rs-title">🏆 섹터별 RS 1위 <span style="font-weight:400;color:var(--text-3);">— 각 섹터 내 상대 강도 최고 종목</span></div>
    <div class="sector-rs-grid">${chips}</div>
  </div>`;
}

// ── 실전 타율 추적 ────────────────────────────────────────────────────────

async function loadPerformance() {
  try {
    const data = await requestJson('/api/performance');
    renderPerformance(data);
  } catch (e) {
    console.warn('타율 데이터 로드 실패:', e);
  }
}

async function updatePerformance() {
  const btn = document.getElementById('btnPerfUpdate');
  if (btn) { btn.textContent = '⏳ 갱신 중…'; btn.disabled = true; }
  try {
    await requestJson('/api/performance/update', { method: 'POST' });
    await loadPerformance();
  } catch (e) {
    console.warn('타율 갱신 실패:', e);
  } finally {
    if (btn) { btn.textContent = '🔄 수익률 갱신'; btn.disabled = false; }
  }
}

function renderPerformance(perf) {
  const sumEl = document.getElementById('perfSummary');
  const tbody = document.getElementById('perfTableBody');
  if (!sumEl || !perf) return;

  if (perf.status === 'DATA_INSUFFICIENT') {
    sumEl.innerHTML = `<div style="padding:16px;text-align:center;color:var(--text-3);width:100%;">${perf.message || '성과 추적 데이터 부족'}</div>`;
    if (tbody) tbody.innerHTML = '<tr><td colspan="7" class="perf-empty">데이터 부족</td></tr>';
    return;
  }

  let html = '';
  for (const [label, returns] of Object.entries(perf.by_label || {})) {
    if (returns.return_5d == null && returns.return_10d == null && returns.max_drawdown_10d_close == null) continue;
    const r5 = returns.return_5d != null ? `${returns.return_5d >= 0 ? '+' : ''}${returns.return_5d.toFixed(1)}%` : '-';
    const r10 = returns.return_10d != null ? `${returns.return_10d >= 0 ? '+' : ''}${returns.return_10d.toFixed(1)}%` : '-';
    const mdd = returns.max_drawdown_10d_close != null ? `${returns.max_drawdown_10d_close.toFixed(1)}%` : '-';

    html += `<div class="perf-card" style="min-width: 200px;">
      <span class="tier-badge" style="background:#eef2ff;color:#4338ca;">${label}</span>
      <span class="perf-card-val ${returns.return_5d >= 0 ? 'ret-pos' : 'ret-neg'}">${r5}</span><small class="perf-card-sub">5일 평균</small>
      <span class="perf-card-val ${returns.return_10d >= 0 ? 'ret-pos' : 'ret-neg'}">${r10}</span><small class="perf-card-sub">10일 평균</small>
      <span class="perf-card-val ret-neg" style="color: #ef4444;">${mdd}</span><small class="perf-card-sub">종가 기준 MDD</small>
    </div>`;
  }

  sumEl.innerHTML = html || '<div style="padding:16px;text-align:center;color:var(--text-3);">표시할 성과 데이터가 없습니다.</div>';
  if (tbody) tbody.innerHTML = '<tr><td colspan="7" class="perf-empty">상세 데이터는 요약 카드에 통합되었습니다.</td></tr>';
}

// 페이지 로드 시 타율 데이터 자동 로드
document.addEventListener('DOMContentLoaded', () => { loadPerformance(); });
