// web/src 를 web/dist 로 옮기고, 평가 결과 파일과 실행 기록을 data/ 에 그대로 싣는다.
import { cpSync, existsSync, mkdirSync, readdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';

const root = new URL('..', import.meta.url).pathname;
const dist = join(root, 'web/dist');
rmSync(dist, { recursive: true, force: true });
mkdirSync(join(dist, 'data'), { recursive: true });
cpSync(join(root, 'web/src'), dist, { recursive: true });
const res = join(root, 'eval/results');
for (const f of readdirSync(res)) if (f.endsWith('.json')) cpSync(join(res, f), join(dist, 'data', f));
const names = readdirSync(res).filter((f) => /^answers_.*\.json$/.test(f)).map((f) => f.slice(8, -5));
import('node:fs').then(({ writeFileSync }) => writeFileSync(join(dist, 'data/index.json'), JSON.stringify({ answers: names })));
const traces = join(root, 'eval/traces.json');
if (!existsSync(traces)) throw new Error('eval/traces.json 이 없습니다: make eval 로 실행 기록을 만드세요');
cpSync(traces, join(dist, 'data/traces.json'));
console.log('web/dist 준비 완료');
