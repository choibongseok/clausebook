import { expect, test, type Page } from '@playwright/test';

const ORIGIN = 'http://127.0.0.1:4373';

async function open(page: Page, query = '', firstVisit = false) {
  // 기본은 "이미 본 방문자"(쿠키 있음)로 연다. 첫 방문 동작을 시험할 때만 firstVisit 를 켠다.
  if (!firstVisit) await page.context().addCookies([{ name: 'cb_tour', value: 'seen', url: `${ORIGIN}/clausebook/` }]);
  const errors: string[] = [];
  const external: string[] = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('request', (r) => { if (!r.url().startsWith(ORIGIN) && !r.url().startsWith('data:')) external.push(r.url()); });
  await page.goto('./' + query);
  await expect(page.locator('body[data-ready="true"]')).toBeVisible({ timeout: 15_000 });
  return { errors, external };
}
const scenario = (page: Page, name: string | RegExp) => page.locator('#sc-basic, #sc-hard').getByRole('radio', { name }).click();
const lum = (rgb: number[]) => { const f = (c: number) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2]); };
const ratio = (a: number[], b: number[]) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

test('WEB-001 | 첫 화면 | 제목, 항상 보이는 기록 재생 안내, 콘솔 오류 없음, 외부 요청 없음 | 사이트를 연다 | 제목과 안내 문장이 보이고 콘솔 오류 0, 같은 출처 밖으로 가는 요청 0', async ({ page }) => {
  const { errors, external } = await open(page);
  await expect(page).toHaveTitle('보험 약관 질의응답 모의 테스트 — Clausebook');
  await expect(page.locator('#h-hero')).toHaveText('보험 약관 질의응답 모의 테스트');
  await expect(page.locator('#banner')).toContainText('미리 돌려 둔 실제 실행 기록을 보여 줍니다. 이 화면에서 모델이 새로 답하지는 않습니다.');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  expect(errors).toEqual([]);
  expect(external).toEqual([]);
});

test('WEB-002 | 설명 따라가기 | 첫 방문이면 자동으로 열리고 접으면 쿠키를 남긴다 | 쿠키 없이 연다, 접기를 누른다 | 1/8 장이 보이고, 접으면 cb_tour=seen 쿠키(경로 /clausebook/, 180일)와 다시 열기 버튼이 생긴다', async ({ page }) => {
  await open(page, '', true);
  await expect(page.locator('#tour')).toBeVisible();
  await expect(page.locator('#tour-prog')).toHaveText('1/8');
  await page.locator('#tour-collapse').click();
  await expect(page.locator('#tour')).toBeHidden();
  await expect(page.locator('#tour-open')).toBeVisible();
  const c = (await page.context().cookies()).find((x) => x.name === 'cb_tour')!;
  expect(c.value).toBe('seen');
  expect(c.path).toBe('/clausebook/');
  expect(c.sameSite).toBe('Lax');
  expect(c.expires - Date.now() / 1000).toBeGreaterThan(179 * 86400);
  expect(c.expires - Date.now() / 1000).toBeLessThan(181 * 86400);
});

test('WEB-003 | 설명 따라가기 | 쿠키가 있으면 자동으로 열지 않는다 | cb_tour=seen 쿠키로 연다 | 설명 패널은 닫혀 있고 처음 화면의 설명 따라가기 버튼으로 열 수 있다', async ({ page }) => {
  await open(page);
  await expect(page.locator('#tour')).toBeHidden();
  await page.locator('#tour-start').click();
  await expect(page.locator('#tour')).toBeVisible();
  await expect(page.locator('#tour-title')).toHaveText('무엇을 만든 것인가요');
});

test('WEB-004 | 설명 따라가기 | 버튼과 방향키로 8장을 넘기고 마치면 닫는다 | 다음 버튼, 오른쪽·왼쪽 방향키, 마치기 | 장 번호가 따라 바뀌고 마지막 장 버튼이 마치기이며 마치면 패널과 다시 열기 버튼이 모두 사라진다', async ({ page }) => {
  await open(page, '', true);
  await page.locator('#tour-next').click();
  await expect(page.locator('#tour-prog')).toHaveText('2/8');
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('#tour-prog')).toHaveText('3/8');
  await page.keyboard.press('ArrowLeft');
  await expect(page.locator('#tour-prog')).toHaveText('2/8');
  for (let i = 2; i < 8; i++) await page.locator('#tour-next').click();
  await expect(page.locator('#tour-prog')).toHaveText('8/8');
  await expect(page.locator('#tour-next')).toHaveText('마치기');
  await page.locator('#tour-next').click();
  await expect(page.locator('#tour')).toBeHidden();
  await expect(page.locator('#tour-open')).toBeHidden();
});

test('WEB-005 | 설명 따라가기 | 장마다 해당 질문을 고르고 볼 곳을 강조한다 | 4장(약관에 없는 질문)으로 이동 | 질문이 바뀌고 답 패널에 강조 표시가 생긴다', async ({ page }) => {
  await open(page, '', true);
  for (let i = 0; i < 3; i++) await page.locator('#tour-next').click();
  await expect(page.locator('#tour-title')).toHaveText('약관에 없는 질문');
  await expect(page.locator('#q-text')).toContainText('암 진단비');
  await expect(page.locator('#p-result')).toHaveClass(/tour-hl/);
});

test('WEB-006 | 질문 고르기 | 약관에 없는 질문은 못 찾았다고 답하고 인용이 없다 | 일부러 어려운 경우에서 약관에 없는 질문 선택 | 판정 문구, 답, 인용 조항 없음 표시', async ({ page }) => {
  await open(page);
  await scenario(page, /약관에 없는 질문/);
  await expect(page.locator('#verdict-title')).toHaveText('약관에서 찾지 못했다고 답했습니다');
  await expect(page.locator('#answer')).toContainText('약관에서 찾지 못했습니다');
  await expect(page.locator('#cites')).toContainText('인용 조항 없음');
});

test('WEB-007 | 멀티턴 | 대화 순서 버튼으로 되묻기와 이어진 답을 모두 볼 수 있다 | 개정 전후가 다른 조항 선택 후 1번째·2번째 질문 | 1번째는 되물음, 2번째는 30%와 개정 조항 인용', async ({ page }) => {
  await open(page);
  await scenario(page, /개정 전후가 다른 조항/);
  await expect(page.locator('#turn-box')).toBeVisible();
  await expect(page.locator('#verdict-title')).toHaveText('조건이 모자라 되물었습니다');
  await page.locator('#turns').getByRole('radio', { name: '2번째 질문' }).click();
  await expect(page.locator('#q-text')).toHaveText('2025년 9월에 가입했어요.');
  await expect(page.locator('#answer')).toContainText('30%');
  await expect(page.locator('#cites')).toContainText('SIL-T#제9조@v2');
  await expect(page.locator('#src-list')).toContainText('2025년 7월 1일 이후 체결한 계약');
});

test('WEB-008 | 처리 순서 | 검색 단계에 찾은 조항과 점수가 있고 막은 요청은 모델 호출이 0회다 | 청약철회 기간 질문, 지시 무시 요청 | 검색 카드에 조항 목록, 막은 요청은 입력 가드 카드와 모델 호출 0회', async ({ page }) => {
  await open(page);
  await scenario(page, /청약철회 기간/);
  const search = page.locator('#timeline li').filter({ has: page.locator('.step-name', { hasText: /^검색$/ }) });
  await expect(search.locator('.hits li').first()).toContainText('TRM-T#제12조');
  await expect(page.locator('#timeline li .step-name')).toContainText(['입력 가드', '분류', '검색', '근거 충분성', '답변 생성', '답변 검수', '출력 가드']);
  await scenario(page, /지시 무시 요청/);
  await expect(page.locator('#verdict-title')).toHaveText('요청을 막았습니다');
  await expect(page.locator('#p-stats .stat').filter({ hasText: '모델 호출' }).locator('.v')).toHaveText('0회');
  await expect(page.locator('#timeline li').first()).toContainText('역할 탈취');
});

test('WEB-009 | 개인정보 | 화면에는 가린 질문만 보이고 원문이 없다 | 개인정보가 든 질문 선택 | 질문과 처리 기록 어디에도 주민등록번호 원문이 없다', async ({ page }) => {
  await open(page);
  await scenario(page, /개인정보가 든 질문/);
  await expect(page.locator('#q-text')).toContainText('[주민등록번호 가림]');
  const text = await page.locator('body').innerText();
  expect(text).not.toContain('1234567');
  expect(text).not.toContain('900101');
});

test('WEB-010 | 평가 표 | 모델 비교표와 검색 구성표가 결과 파일의 값과 같다 | 평가 결과 JSON 을 읽어 표와 대조 | 모델 행 수와 통과율, 검색 구성 24행과 1등 nDCG 가 같다', async ({ page }) => {
  await open(page);
  const idx = await (await page.request.get('./data/index.json')).json();
  const matrix = await (await page.request.get('./data/retrieval_matrix.json')).json();
  await expect(page.locator('#models tbody tr')).toHaveCount(idx.answers.length);
  for (const n of idx.answers) {
    const d = await (await page.request.get(`./data/answers_${n}.json`)).json();
    const row = page.locator('#models tbody tr', { hasText: d.summary ? '' : '' }).filter({ has: page.locator('td', { hasText: `${(d.summary.pass_rate * 100).toFixed(1)}%` }) });
    await expect(row.first()).toBeVisible();
  }
  await expect(page.locator('#retr tbody tr')).toHaveCount(matrix.configs.length);
  const best = Math.max(...matrix.configs.map((c: any) => c['ndcg@5']));
  await expect(page.locator('#retr tbody tr').first().locator('td').nth(6)).toHaveText(best.toFixed(3));
  await expect(page.locator('#models tbody tr', { hasText: '추출 기준선' })).toHaveCount(1);
});

test('WEB-011 | 테마 | 라이트로 바꾸고 다시 열어도 유지된다 | ◐ 버튼, 새로고침 | data-theme 이 light 이고 새로고침 뒤에도 light', async ({ page }) => {
  await open(page);
  await page.locator('#theme').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.reload();
  await expect(page.locator('body[data-ready="true"]')).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});

test('WEB-012 | 대비 | 보조 글자와 본문 글자가 배경 대비 4.5:1 이상이다 | 다크·라이트 테마에서 안내 글, 표 머리, 단계 시간, 본문, 모든 상태 색 | 최소 대비 4.5 이상', async ({ page }) => {
  await open(page);
  const measure = async () => page.evaluate(() => {
    const rgb = (s: string) => (s.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
    const bg = (e: Element) => { let n: Element | null = e; while (n) { const c = getComputedStyle(n).backgroundColor; if (!/rgba?\(0, 0, 0, 0\)/.test(c) && c !== 'transparent') return rgb(c); n = n.parentElement; } return [255, 255, 255]; };
    const sel = ['.hint', '.sub', '.legend', '.step-time', '.ptitle', 'th', '.stat .k', '.what', '.easy', '.step-detail', '.tag', '.chip', '.banner strong', '#tour-title', '.primary'];
    return sel.map((s) => { const e = document.querySelector(s)!; return { s, fg: rgb(getComputedStyle(e).color), bg: bg(e) }; });
  });
  for (const theme of ['dark', 'light']) {
    await page.evaluate((t) => { document.documentElement.dataset.theme = t; }, theme);
    for (const m of await measure()) expect(ratio(m.fg, m.bg), `${theme} ${m.s}`).toBeGreaterThanOrEqual(4.5);
  }
});

test('WEB-013 | 키보드 | 라디오 묶음 안에서 화살표 키로 질문을 옮길 수 있다 | 첫 질문 버튼에 초점을 두고 오른쪽 화살표 | 다음 질문이 선택된다', async ({ page }) => {
  await open(page);
  await page.locator('#sc-basic button').first().focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('#sc-basic button').nth(1)).toHaveAttribute('aria-checked', 'true');
  await expect(page.locator('#sc-basic button').first()).toHaveAttribute('aria-checked', 'false');
});

test('WEB-014 | 모바일 | 390px 폭에서 가로 스크롤이 없고 설명은 하단 시트로 열린다 | 폭 390, 첫 방문 | 문서 폭이 화면 폭 이하, 설명 패널이 화면 아래에 붙고 높이가 화면의 절반 이하', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page, '', true);
  const w = await page.evaluate(() => ({ doc: document.documentElement.scrollWidth, win: window.innerWidth }));
  expect(w.doc).toBeLessThanOrEqual(w.win);
  const box = (await page.locator('#tour').boundingBox())!;
  expect(box.y + box.height).toBeGreaterThanOrEqual(843);
  expect(box.height).toBeLessThanOrEqual(844 * 0.5);
});

test('WEB-015 | 근거 보기 | 인용한 조항의 원문과 시행 기간이 아래에 보인다 | 청약철회 기간 질문 선택 | 근거 조항 원문에 15일이 든 제12조가 있다', async ({ page }) => {
  await open(page);
  await scenario(page, /청약철회 기간/);
  await expect(page.locator('#src-list .srcitem').first()).toContainText('제12조');
  await expect(page.locator('#src-list')).toContainText('15일');
});

test('WEB-016 | 문서 속 주입 | 문서에 심은 지시문은 근거에서 제외했다고 보여 주고 답에 따르지 않는다 | 문서에 심은 지시문 선택 | 안내 문구 카드에 제외한 조각, 답에 전액 보장이라는 말이 없다', async ({ page }) => {
  await open(page);
  await scenario(page, /문서에 심은 지시문/);
  await expect(page.locator('#timeline')).toContainText('TRV-F:Q5');
  await expect(page.locator('#answer')).not.toContainText('전액 보장');
});
