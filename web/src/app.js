// 미리 돌려 둔 실행 기록(data/traces.json)과 평가 결과 파일(data/*.json)을 읽어 보여 준다. 서버도 모델도 부르지 않는다.
const $ = (s) => document.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'text') e.textContent = v; else if (k === 'class') e.className = v; else e.setAttribute(k, v);
  }
  for (const c of kids) if (c != null) e.append(c);
  return e;
};
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const pct = (x) => (x == null ? '-' : `${(x * 100).toFixed(1)}%`);
const sec = (ms) => `${(ms / 1000).toFixed(2)}초`;

const INTENT = { clause_lookup: '약관 조회', calculation: '보험금·환급금 계산', claim_docs: '청구 서류·절차', individual_review: '개별 사고의 지급 여부', out_of_scope: '보험 약관과 관계없음', smalltalk: '인사', injection: '역할 탈취·지시 무시 요청' };
const STATUS = {
  answered: ['답을 찾았습니다', 'ok'], calculated: ['도구로 계산했습니다', 'ok'], not_found: ['약관에서 찾지 못했다고 답했습니다', 'warn'],
  clarify: ['조건이 모자라 되물었습니다', 'warn'], guidance: ['지급 여부는 확정하지 않고 안내했습니다', 'warn'], refused: ['범위 밖이라 답하지 않았습니다', 'warn'], blocked: ['요청을 막았습니다', 'ok'],
};
const STRAT = { fixed: '고정 길이', clause: '조항 단위', clause_ctx: '조항 + 상위 문맥' };
const METHOD = { dense: '밀집', bm25: 'BM25', hybrid_rrf: '하이브리드(순위 결합)', hybrid_weighted: '하이브리드(점수 가중)' };
const RERANK = { none: '없음', rule: '규칙' };
const MODEL_LABEL = { 'mock-extractive': '추출 기준선(모델 없음)', 'mock-sloppy': '모의: 부정확형', 'mock-fabricator': '모의: 지어내기형', 'qwen2.5-0.5b-prompt-v1': 'Qwen2.5-0.5B Q4_K_M, 프롬프트 v1', 'qwen2.5-0.5b': 'Qwen2.5-0.5B Q4_K_M, 프롬프트 v2', 'qwen2.5-1.5b': 'Qwen2.5-1.5B Q4_K_M, 프롬프트 v2' };
const SLOT = { product: '상품', join_date: '계약일', diagnosis_date: '진단확정일', setting: '구분', hospital_type: '병원 종류', cancer_type: '암 종류', covered_copay: '급여 본인부담금', uncovered: '비급여', monthly_premium: '월 보험료', years_elapsed: '경과 연수', years_used: '사용 연수', purchase_price: '구입가', cause: '사고 원인', days: '입원 일수', age: '나이' };
const PRODUCT = { SIL: '한결 실손의료보험', TRM: '든든 정기보험', CAN: '마음 암진단보험', DRV: '길동무 운전자보험', KID: '새싹 어린이보험', TRV: '나래 여행자보험' };

const S = { traces: null, sc: null, turn: 0 };

async function getJson(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path} ${r.status}`);
  return r.json();
}

// ---------- 단계 카드 ----------
function kv(k, v) { return el('div', {}, el('span', { class: 'kv', text: `${k} ` }), String(v)); }
function jsonBox(o) { return el('pre', { class: 'json', text: JSON.stringify(o, null, 1) }); }
function hitsList(hits) {
  const max = Math.max(...hits.map((h) => h.score), 0.0001);
  return el('ul', { class: 'hits' }, ...hits.slice(0, 5).map((h) => el('li', {}, el('span', { text: h.id }), el('span', { text: h.score.toFixed(3) }), el('div', { class: 'bar' }, el('i', { style: `width:${Math.max(4, (h.score / max) * 100)}%` })))));
}
function detailOf(s) {
  const d = s.detail || {};
  const box = el('div', { class: 'step-detail' });
  let tone = 'ok';
  switch (s.stage) {
    case '입력 가드': {
      const pii = Object.entries(d.masked || {}).map(([k, n]) => `${k} ${n}건`);
      if (d.injection?.length) { box.append(kv('판정', `지시 무시·역할 탈취 요청으로 보고 막음 (${d.injection.join(', ')})`)); tone = 'warn'; }
      else if (pii.length) { box.append(kv('개인정보', `${pii.join(', ')}을 가림`), kv('가린 뒤 질문', d.text)); tone = 'warn'; }
      else box.append(kv('판정', '개인정보와 주입 표현이 없습니다.'));
      break;
    }
    case '분류':
      box.append(kv('분류', INTENT[d.intent] || d.intent), kv('상품', PRODUCT[d.product] || '아직 모름'));
      if (d.tool) box.append(kv('쓸 도구', d.tool));
      if (d.resumed) box.append(kv('이어서 풀기', d.resumed === 'version' ? '되묻기에 대한 답을 받아 보류한 질문을 다시 풂' : '상품을 받아 보류한 질문을 다시 풂'));
      if (Object.keys(d.slots || {}).length) box.append(kv('질문에서 뽑은 조건', Object.entries(d.slots).map(([k, v]) => `${SLOT[k] || k}=${PRODUCT[v] || v}`).join(', ')));
      break;
    case '질문 정리':
      box.append(kv(d.rewritten ? '앞 질문과 합침' : '검색어', d.query));
      break;
    case '검색': {
      box.append(kv('방식', `${STRAT[d.strategy]} · ${METHOD[d.method]} · 재정렬 ${RERANK[d.rerank]}`));
      const f = d.filter || {};
      box.append(kv('필터', [f.products ? `상품 ${f.products.map((p) => PRODUCT[p]).join(', ')}` : '상품 제한 없음', f.as_of ? `계약일 ${f.as_of} 기준 유효 조항` : null, f.doc_types ? `문서 ${f.doc_types.join('·')}` : null].filter(Boolean).join(' / ')));
      if (d.hits?.length) box.append(hitsList(d.hits));
      break;
    }
    case '근거 충분성':
      box.append(kv('질문 핵심어 포함률', d.coverage), kv('가장 가까운 조항과의 유사도', d.dense), kv('판정', d.pass ? '근거 있음' : '근거 부족 → 못 찾았다고 답함'));
      if (!d.pass) tone = 'warn';
      break;
    case '개정 확인':
      if (d.conflict?.length) { box.append(kv('판정', `${d.conflict.join(', ')} 은(는) 계약일에 따라 내용이 다른 개정 조항입니다.`)); tone = 'warn'; }
      else box.append(kv('판정', d.as_of ? `계약일 ${d.as_of} 기준으로 유효한 조항만 사용` : '개정 전후 내용이 갈리는 조항이 아닙니다.'));
      break;
    case '안내 문구':
      box.append(kv('이유', d.reason));
      if (d.removed_chunks) box.append(kv('제외한 문서 조각', d.removed_chunks.join(', ')));
      if (d.clauses) box.append(kv('안내에 붙인 조항', d.clauses.join(', ')));
      tone = 'warn';
      break;
    case '답변 생성':
      box.append(kv('모델', `${MODEL_LABEL[d.model] || d.model} · 프롬프트 ${d.prompt_version}`), kv('모델에 준 근거', Object.entries(d.context || {}).map(([t, c]) => `${t}=${c.split(':').slice(1).join(':')}`).join(', ')));
      if (d.fallback) { box.append(kv('대체', `모델 출력이 형식에 맞지 않아 추출 답변으로 대체: ${d.fallback}`)); tone = 'warn'; }
      if (d.attempts > 1) { box.append(kv('시도', `${d.attempts}번 만에 형식에 맞는 출력`)); tone = 'warn'; }
      if (d.output) box.append(jsonBox(d.output));
      break;
    case '답변 검수':
      if (d.model_said_unanswerable) { box.append(kv('판정', '모델이 근거 부족이라고 답함')); tone = 'warn'; }
      else {
        box.append(kv('남긴 문장', (d.kept || []).length));
        for (const x of d.dropped || []) { box.append(el('div', { class: 'del', text: x.sentence }), kv('뺀 이유', x.reason)); tone = 'warn'; }
        if (d.invalid_citations?.length) { box.append(kv('버린 인용', d.invalid_citations.join(', '))); tone = 'warn'; }
        if (d.repaired) box.append(kv('인용 보정', `${d.repaired}문장`));
        box.append(kv('최종 인용', (d.citations || []).join(', ') || '없음'));
      }
      break;
    case '도구 호출 생성':
      box.append(kv('모델', MODEL_LABEL[d.model] || d.model), kv('기대한 도구', d.expected_tool), kv('모델 호출', d.model_call_valid ? '조건과 일치' : `검증 실패 → 규칙으로 뽑은 값 사용 (${d.fallback || ''})`), jsonBox(d.arguments));
      if (!d.model_call_valid) tone = 'warn';
      break;
    case '도구 실행':
      if (d.error) { box.append(kv('오류', d.error)); tone = 'error'; }
      else box.append(kv('결과', `${Number(d.result).toLocaleString('ko-KR')}원`), ...(d.steps || []).map((x, i) => kv(`${i + 1})`, x)));
      break;
    case '계산 결과 정리':
      box.append(kv('근거 조항', (d.sources || []).join(', ')));
      break;
    case '되묻기':
      box.append(kv('되묻는 것', d.ask === 'product' ? '상품' : d.ask === 'calc_slots' ? `계산 조건: ${(d.missing || []).map((m) => SLOT[m] || m).join(', ')}` : d.ask));
      tone = 'warn';
      break;
    case '출력 가드':
      box.append(kv('최종 상태', (STATUS[d.status] || [d.status])[0]));
      if (Object.keys(d.masked_in_output || {}).length) { box.append(kv('답에서 가린 것', Object.keys(d.masked_in_output).join(', '))); tone = 'warn'; }
      break;
    default:
      box.append(jsonBox(d));
  }
  return [box, tone];
}

function renderTurn() {
  const sc = S.sc;
  const t = sc.turns[S.turn];
  const r = t.result;
  const [title, tone] = STATUS[r.status] || [r.status, 'warn'];
  $('#p-result').dataset.tone = tone;
  $('#q-text').textContent = t.user;
  $('#verdict-title').textContent = title;
  $('#answer').textContent = r.answer;
  $('#cites').replaceChildren(...(r.citations.length ? r.citations.map((c) => el('span', { class: 'chip', title: c.heading, text: c.id + (c.effective ? ' (개정 조항)' : '') })) : [el('span', { class: 'chip warnchip', text: '인용 조항 없음' })]));
  $('#meta').textContent = `질문 분류: ${INTENT[r.intent] || r.intent} · 요청 ID ${r.trace_id}`;
  const llm = r.steps.filter((s) => ['답변 생성', '도구 호출 생성'].includes(s.stage));
  const stats = [
    ['전체 처리 시간', sec(r.total_ms)], ['모델 호출', `${r.usage.llm_calls}회`],
    ['입력 토큰', r.usage.prompt_tokens.toLocaleString('ko-KR')], ['출력 토큰', r.usage.completion_tokens.toLocaleString('ko-KR')],
    ['모델이 쓴 시간', sec(llm.reduce((a, s) => a + s.ms, 0))], ['검색에 쓴 시간', `${(r.steps.find((s) => s.stage === '검색')?.ms ?? 0).toFixed(1)}ms`],
  ];
  $('#p-stats').replaceChildren(...stats.map(([k, v]) => el('div', { class: 'stat' }, el('span', { class: 'k', text: k }), el('span', { class: 'v', text: v }))));
  $('#tl-title').textContent = `처리 순서 (${S.traces.model_label})`;
  const cards = r.steps.map((s) => {
    const [box, tn] = detailOf(s);
    return [el('li', { 'data-tone': tn }, el('div', { class: 'step-head' }, el('span', { class: 'step-name', text: s.stage }), el('span', { class: 'step-time', text: s.ms >= 1000 ? sec(s.ms) : `${s.ms.toFixed(1)}ms` })), box), tn];
  });
  $('#timeline').replaceChildren(...cards.map((c) => c[0]));
  $('#strip').replaceChildren(...cards.map((c) => el('span', { 'data-tone': c[1] })));
  $('#src-list').replaceChildren(...(r.citations.length ? r.citations.map((c) => el('div', { class: 'srcitem' }, el('h3', { text: `${c.doc_title} · ${c.heading}${c.effective ? ` · 시행 ${c.effective}` : ''}` }), el('p', { text: c.text }))) : [el('p', { class: 'hint', text: '이 답에는 인용한 조항이 없습니다.' })]));
}

function pickTurn(i) {
  S.turn = i;
  const sc = S.sc;
  $('#turns').replaceChildren(...sc.turns.map((t, j) => {
    const b = el('button', { type: 'button', role: 'radio', 'aria-checked': String(j === i), text: `${j + 1}번째 질문` });
    b.addEventListener('click', () => pickTurn(j));
    return b;
  }));
  renderTurn();
}
function pick(id, turn = 0) {
  const sc = S.traces.scenarios.find((x) => x.id === id);
  S.sc = sc;
  for (const b of document.querySelectorAll('#sc-basic button, #sc-hard button')) b.setAttribute('aria-checked', String(b.dataset.id === id));
  $('#sc-desc').textContent = sc.desc;
  $('#turn-box').hidden = sc.turns.length < 2;
  pickTurn(Math.min(turn, sc.turns.length - 1));
}
function buildPicker() {
  for (const [grp, box] of [['basic', '#sc-basic'], ['hard', '#sc-hard']]) {
    $(box).replaceChildren(...S.traces.scenarios.filter((s) => s.group === grp).map((s) => {
      const b = el('button', { type: 'button', role: 'radio', 'aria-checked': 'false', 'data-id': s.id }, s.title, el('small', { text: s.sub }));
      b.addEventListener('click', () => pick(s.id));
      return b;
    }));
  }
}

// ---------- 평가 표 ----------
function table(sel, head, rows, opts = {}) {
  const t = $(sel);
  t.querySelector('thead').replaceChildren(el('tr', {}, ...head.map((h, i) => el('th', { class: i >= (opts.numFrom ?? 1) ? 'num' : '', text: h }))));
  t.querySelector('tbody').replaceChildren(...rows.map((r) => el('tr', { class: r.best ? 'best' : '' }, ...r.cells.map((c, i) => el('td', { class: i >= (opts.numFrom ?? 1) ? 'num' : '', text: c })))));
}
async function renderEval() {
  const idx = await getJson('data/index.json');
  const matrix = await getJson('data/retrieval_matrix.json');
  const answers = [];
  for (const n of idx.answers) answers.push(await getJson(`data/answers_${n}.json`));
  const order = ['mock-extractive', 'qwen2.5-0.5b-prompt-v1', 'qwen2.5-0.5b', 'qwen2.5-1.5b', 'mock-sloppy', 'mock-fabricator'];
  answers.sort((a, b) => order.indexOf(a.name) - order.indexOf(b.name));
  const n = answers[0].dataset.n_items;
  $('#n-questions').textContent = String(n);
  $('#eval-hint').textContent = `질문 ${n}개를 모델마다 같은 조건으로 돌린 결과 파일(eval/results/answers_*.json)의 값입니다. 통과는 답 유형이 맞고, 정답 요소가 모두 들어 있고, 정답 조항을 인용했고, 금지 표현이 없는 경우입니다. 모의 모델 두 줄은 평가 체계가 나쁜 답을 나쁘게 채점하는지 확인하려고 넣은 것이며 실제 모델 성능이 아닙니다.`;
  const cols = [['통과율', 'pass_rate'], ['정답 요소 포함', 'element_recall'], ['인용 적중', 'citation_hit_rate'], ['인용 정밀도', 'citation_precision'], ['근거 밖 숫자 답변', 'unsupported_number_rate'], ['거절 정확도', 'refusal_accuracy'], ['거짓 거절', 'false_refusal_rate'], ['계산 정확', 'calc_exact']];
  const bestPass = Math.max(...answers.filter((a) => !a.name.startsWith('mock-s') && !a.name.startsWith('mock-f')).map((a) => a.summary.pass_rate));
  table('#models', ['모델', ...cols.map((c) => c[0]), '응답 p50', '응답 p95', '100건당 예상 비용($)'], answers.map((a) => ({
    best: a.summary.pass_rate === bestPass && !a.name.startsWith('mock-s') && !a.name.startsWith('mock-f'),
    cells: [MODEL_LABEL[a.name] || a.name, ...cols.map((c) => pct(a.summary[c[1]])), sec(a.summary.latency_ms_p50), sec(a.summary.latency_ms_p95), a.summary.cost_usd_per_100.toFixed(4)],
  })));
  const cats = Object.keys(answers[0].summary.by_category);
  const catNames = { lookup: '단순 조회', exception: '예외 조항', table: '표 조회', multi: '여러 조항 결합', calc: '계산', followup: '멀티턴 후속', notfound: '답이 문서에 없음', injection: '주입 시도', pii: '개인정보 포함', version: '개정 버전', individual: '개별 심사', claims: '청구 안내', paraphrase: '돌려 말하기' };
  table('#cats', ['유형', '질문 수', ...answers.map((a) => MODEL_LABEL[a.name] || a.name)], cats.map((c) => ({ cells: [catNames[c] || c, String(answers[0].summary.by_category[c].n), ...answers.map((a) => pct(a.summary.by_category[c]?.pass_rate))] })), { numFrom: 1 });
  $('#retr-hint').textContent = `검색 평가 대상 질문 ${matrix.dataset.n_retrieval_items}개(정답 조항이 있는 질문). 정답 조항 id 로 채점해서 분할 방식이 달라도 같은 기준입니다. 고정 길이 청크는 여러 조항을 한 번에 덮어서 Recall@5 가 높게 나오는 경향이 있고, 순위 지표(MRR, nDCG)에서 차이가 납니다. 표는 nDCG@5 순입니다. 마지막 열은 구성을 고를 때 쓰지 않은 보류 질문만의 값입니다.`;
  const sorted = [...matrix.configs].sort((a, b) => b['ndcg@5'] - a['ndcg@5']);
  table('#retr', ['분할', '검색', '재정렬', 'Recall@1', 'Recall@5', 'MRR', 'nDCG@5', 'nDCG@5 (보류 집합)'], sorted.map((c, i) => ({ best: i === 0, cells: [STRAT[c.strategy], METHOD[c.method], RERANK[c.rerank], ...['recall@1', 'recall@5', 'mrr', 'ndcg@5'].map((k) => c[k].toFixed(3)), c.test['ndcg@5'].toFixed(3)] })), { numFrom: 3 });
  const env = answers[0].env;
  const lim = S.traces.limits || [];
  $('#limits').replaceChildren(
    el('li', {}, el('strong', { text: '측정 환경' }), el('span', { text: `${S.traces.environment}. 응답 시간은 CPU 추론이고 다른 컨테이너가 같이 돌아서 참고용입니다.` })),
    ...lim.map((l) => el('li', {}, el('strong', { text: l.title }), el('span', { text: l.text }))),
  );
  void env;
}

// ---------- 설명 따라가기 ----------
const TOUR = [
  { title: '무엇을 만든 것인가요', text: ['보험 약관을 읽고 질문에 답하는 상담 도우미입니다. 질문이 들어오면 분류하고, 약관에서 관련 조항을 찾고, 답을 만들고, 답이 조항에 실제로 있는지 검사합니다.', '아래 화면은 미리 돌려 둔 실제 실행 기록입니다. 여기서 모델이 새로 답하지는 않습니다.'], look: '맨 위의 "기록 재생" 안내와 제목 아래 설명', hl: ['#banner', '.hero'] },
  { title: '질문 하나가 처리되는 순서', text: ['먼저 보통 질문을 하나 골랐습니다. 아래 처리 순서 카드를 왼쪽 위부터 읽으면 한 질문이 지나간 단계와 단계별 시간이 보입니다.'], look: '"처리 순서"의 카드들. 검색 카드에는 찾은 조항과 점수가 있습니다', hl: ['#p-timeline'], sc: 'lookup' },
  { title: '답마다 근거 조항을 붙입니다', text: ['답 아래의 조항 이름을 누르는 대신 맨 아래 "근거 조항 원문"을 보면 인용한 조항의 원문이 있습니다. 모델이 쓴 문장이 이 원문에 있는 내용인지 검수 단계가 확인합니다.'], look: '"답변 검수" 카드와 "근거 조항 원문"', hl: ['#p-src'], sc: 'lookup' },
  { title: '약관에 없는 질문', text: ['약관에 없는 내용을 물으면 그럴듯하게 지어내지 않고 못 찾았다고 답해야 합니다. 이 질문은 정기보험에 암 진단비가 있는지 묻습니다.'], look: '답의 "약관에서 찾지 못했습니다"와 인용이 비어 있는 것', hl: ['#p-result'], sc: 'notfound' },
  { title: '개정 전후가 다른 조항', text: ['같은 상품도 가입 시기에 따라 적용되는 조항이 다릅니다. 가입 시기를 모르면 되묻고, 답을 받으면 그 시기에 유효한 조항만 찾습니다.', '대화 순서를 1번째, 2번째로 바꿔 보세요.'], look: '"대화 순서" 버튼과 "개정 확인" 카드', hl: ['#turn-box', '#p-timeline'], sc: 'version', turn: 1 },
  { title: '계산은 도구가 합니다', text: ['금액 계산은 모델이 하지 않고 코드로 만든 계산 도구가 합니다. 모델은 어떤 도구를 어떤 값으로 부를지 정할 뿐이고, 값이 질문에 없으면 다시 요청하거나 규칙으로 뽑은 값을 씁니다.'], look: '"도구 호출 생성"과 "도구 실행" 카드', hl: ['#p-timeline'], sc: 'calc' },
  { title: '막아야 하는 입력', text: ['"이전 지시를 무시하라"는 요청은 검색도 모델 호출도 하기 전에 막습니다. 주민등록번호 같은 개인정보는 가려서 처리하고, 답과 기록에도 남기지 않습니다.'], look: '"입력 가드" 카드. 모델 호출이 0회입니다', hl: ['#p-timeline'], sc: 'injection' },
  { title: '구성과 모델을 숫자로 비교합니다', text: ['같은 질문 묶음으로 분할 방식, 검색 방식, 재정렬, 생성 모델을 바꿔 가며 쟀습니다. 표의 값은 실행 결과 파일 그대로입니다.', '맨 아래에는 측정 환경과 이 평가로 알 수 없는 것을 적었습니다.'], look: '"답변 평가: 모델 비교"와 "검색 구성 비교" 표', hl: ['#p-eval', '#p-retr'] },
];
const tourMs = Number(new URLSearchParams(location.search).get('tourms')) || 8000;
const tour = { open: false, i: 0, auto: false, timer: null };
// 첫 방문 여부는 쿠키 하나(cb_tour=seen)로만 기억한다. 식별 정보는 없고, 쿠키를 쓸 수 없으면 매번 자동으로 연다.
function hasSeen() { try { return /(?:^|;\s*)cb_tour=seen(?:;|$)/.test(document.cookie); } catch { return false; } }
function markSeen() { try { document.cookie = 'cb_tour=seen; path=/clausebook/; max-age=15552000; SameSite=Lax'; } catch { /* 쿠키를 쓸 수 없는 환경 */ } }
function highlight(sels) {
  document.querySelectorAll('.tour-hl').forEach((e) => e.classList.remove('tour-hl'));
  for (const sel of sels || []) document.querySelectorAll(sel).forEach((e) => e.classList.add('tour-hl'));
}
function tourTimer() {
  clearTimeout(tour.timer);
  if (!tour.open || !tour.auto || reduced || tour.i >= TOUR.length - 1) return;
  tour.timer = setTimeout(() => tourGo(tour.i + 1), tourMs);
}
function sheetHeight() { document.documentElement.style.setProperty('--sheet-h', tour.open ? `${$('#tour').offsetHeight}px` : '0px'); }
function tourGo(i, scroll = true) {
  tour.i = Math.max(0, Math.min(TOUR.length - 1, i));
  const st = TOUR[tour.i];
  if (st.sc && S.traces) pick(st.sc, st.turn || 0);
  highlight(st.hl);
  $('#tour-prog').textContent = `${tour.i + 1}/${TOUR.length}`;
  $('#tour-prog').setAttribute('aria-label', `${TOUR.length}장 중 ${tour.i + 1}장`);
  $('#tour-title').textContent = st.title;
  $('#tour-text').replaceChildren(...st.text.map((t) => el('p', { text: t })));
  $('#tour-look').textContent = `지금 화면에서 볼 곳: ${st.look}`;
  $('#tour-prev').disabled = tour.i === 0;
  $('#tour-next').textContent = tour.i === TOUR.length - 1 ? '마치기' : '다음';
  if (scroll) document.querySelector('.tour-hl')?.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' });
  sheetHeight();
  tourTimer();
}
function tourOpen(i = tour.i, focus = false, scroll = true) {
  tour.open = true;
  $('#tour').hidden = false; $('#tour-open').hidden = true;
  document.body.classList.add('tour-open');
  tourGo(i, scroll);
  if (focus) $('#tour-title').focus({ preventScroll: true });
}
function tourCollapse() {
  markSeen();
  tour.open = false; clearTimeout(tour.timer);
  $('#tour').hidden = true; $('#tour-open').hidden = false;
  document.body.classList.remove('tour-open');
  document.documentElement.style.setProperty('--sheet-h', '0px');
  highlight([]);
}
function tourFinish() { markSeen(); tour.i = 0; tour.auto = false; $('#tour-auto').setAttribute('aria-pressed', 'false'); $('#tour-auto').textContent = '자동 진행 켜기'; tourCollapse(); $('#tour-open').hidden = true; }
$('#tour-start').addEventListener('click', () => { tour.i = 0; tourOpen(0, true); });
$('#tour-skip').addEventListener('click', () => { tourFinish(); $('#h-pick').scrollIntoView({ behavior: reduced ? 'auto' : 'smooth' }); });
$('#tour-open').addEventListener('click', () => tourOpen(tour.i, true));
$('#tour-collapse').addEventListener('click', () => { tourCollapse(); $('#tour-open').focus(); });
$('#tour-prev').addEventListener('click', () => tourGo(tour.i - 1));
$('#tour-next').addEventListener('click', () => (tour.i === TOUR.length - 1 ? tourFinish() : tourGo(tour.i + 1)));
if (reduced) { $('#tour-auto').disabled = true; $('#tour-keys').textContent = '← → 키로 넘길 수 있습니다. (동작 줄이기 설정이라 자동 진행은 꺼져 있습니다)'; }
$('#tour-auto').addEventListener('click', () => {
  tour.auto = !tour.auto;
  $('#tour-auto').setAttribute('aria-pressed', String(tour.auto));
  $('#tour-auto').textContent = tour.auto ? '자동 진행 끄기' : '자동 진행 켜기';
  tourTimer();
});
document.addEventListener('keydown', (e) => {
  if (!tour.open || e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) return;
  if (document.activeElement?.closest('[role="radiogroup"]')) return;
  if (e.key === 'ArrowRight') { e.preventDefault(); tour.i === TOUR.length - 1 ? tourFinish() : tourGo(tour.i + 1); }
  else if (e.key === 'ArrowLeft') { e.preventDefault(); tourGo(tour.i - 1); }
});
new ResizeObserver(sheetHeight).observe($('#tour'));

// ---------- 테마 ----------
const root = document.documentElement;
let saved = null;
try { saved = localStorage.getItem('cb-theme'); } catch { /* 저장소를 못 쓰는 환경 */ }
root.dataset.theme = saved === 'light' ? 'light' : 'dark';
$('#theme').addEventListener('click', () => {
  root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
  try { localStorage.setItem('cb-theme', root.dataset.theme); } catch { /* 무시 */ }
});

// 라디오 묶음 안에서 화살표 키로 옮기기
document.addEventListener('keydown', (e) => {
  const g = document.activeElement?.closest?.('[role="radiogroup"]');
  if (!g || !['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp'].includes(e.key)) return;
  const bs = [...g.querySelectorAll('button')];
  const i = bs.indexOf(document.activeElement);
  const n = bs[(i + (e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : bs.length - 1)) % bs.length];
  e.preventDefault(); n.focus(); n.click();
});

(async () => {
  try {
    S.traces = await getJson('data/traces.json');
    buildPicker();
    pick(S.traces.scenarios[0].id);
    await renderEval();
    if (!hasSeen()) tourOpen(0, false, false);
    document.body.dataset.ready = 'true';
  } catch (e) {
    $('#answer').textContent = '실행 기록을 읽지 못했습니다: ' + e.message;
    document.body.dataset.ready = 'true';
    document.body.dataset.error = e.message;
  }
})();
