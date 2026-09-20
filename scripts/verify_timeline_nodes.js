// M13 增量验证 harness（不进仓库，放临时目录）
// 目的：把 web/src/utils/timeline.ts **真正执行一遍**，覆盖
//   ① 实时 WS 路径 wsEventToNode —— 4 种引擎事件 + 降级/正常闸
//   ② 快照重建路径 engineEventToNode / gateReviewToNode
// 不依赖浏览器、不消耗任何模型配额。
const fs = require('fs');
const path = require('path');
const os = require('os');
const ts = require('typescript');

const WEB = process.env.WEB_ROOT;
const SRC = path.join(WEB, 'src');
const OUT = path.join(os.tmpdir(), 'tlcheck_out');
fs.mkdirSync(OUT, { recursive: true });

function transpile(file, out) {
  let code = fs.readFileSync(file, 'utf8');
  // 别名 → 相对（仅这两处有运行时依赖；其余 import 全是 import type，转译后自动消失）
  code = code.replace(/@\/utils\/reviewStatus/g, './reviewStatus');
  code = code.replace(/@\/types/g, './types');
  const js = ts.transpileModule(code, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  fs.writeFileSync(path.join(OUT, out), js);
}
transpile(path.join(SRC, 'utils', 'reviewStatus.ts'), 'reviewStatus.js');
transpile(path.join(SRC, 'utils', 'timeline.ts'), 'timeline.js');

const tl = require(path.join(OUT, 'timeline.js'));

let pass = 0, fail = 0;
function check(name, actual, expected) {
  // 注意：对象不能用 === 比较（引用恒不等）——序列化后再比（本 harness 初版就踩了这个坑，
  // 误报了 4 条 FAIL，而 actual 与 expect 逐字相同）。
  const a = typeof actual === 'object' ? JSON.stringify(actual) : String(actual);
  const e = typeof expected === 'object' ? JSON.stringify(expected) : String(expected);
  const ok = a === e;
  ok ? pass++ : fail++;
  console.log(`  ${ok ? '✅' : '❌'} ${name}\n       actual=${JSON.stringify(actual)}${ok ? '' : `\n       expect=${JSON.stringify(expected)}`}`);
}

const ev = (t, p) => ({ event_type: t, task_id: 't', timestamp: '2026-09-19T15:00:00', ...p });

console.log('=== ① 实时路径 wsEventToNode：引擎事件不再「未知事件」 ===');
const liveCases = [
  ['researcher_records_synthesized', { event: 'researcher_records_synthesized', agent: 'Researcher', count: 51, round: 1 },
    { title: '🧩 检索记录由引擎合成', status: 'info', type: 'agent' }],
  ['writer_citation_regenerated', { event: 'writer_citation_regenerated', round: 1 },
    { title: '🔗 引用链已重建', status: 'warning', type: 'agent' }],
  ['gate_llm_unavailable_degraded_advance', { event: 'gate_llm_unavailable_degraded_advance', gate: 'GateA', round: 1 },
    { title: '🔓 闸 LLM 不可用·降级放行', status: 'warning', type: 'gate' }],
  ['agent_output_unusable(既有分支不变)', { event: 'agent_output_unusable', agent: 'Analyst', reason: 'x', round: 1 },
    { title: '⚠️ Agent 产出不可用 · Analyst', status: 'error', type: 'tool_error' }],
];
for (const [name, payload, exp] of liveCases) {
  const n = tl.wsEventToNode(ev(name.split('(')[0], payload));
  check(name, { title: n.title, status: n.status, type: n.type }, exp);
  if (/未知事件/.test(n.title)) { fail++; console.log('       ❌ 仍渲染成未知事件！'); }
}

console.log('\n=== ② 实时路径 gate_complete：降级不得显示成「审核放行」 ===');
const degradedGate = { round: 1, gate: 'GateA', decision: 'advance', eval_score: null, problem_points: ['x'],
  reason: '审核 LLM 不可用（降级放行）：代码硬校验已通过，未见业务致命问题' };
const cleanGate = { round: 1, gate: 'GateA', decision: 'advance', eval_score: 0.9, problem_points: [],
  reason: '检索记录覆盖全部子问题' };
check('降级闸(实时)', tl.wsEventToNode(ev('gate_complete', degradedGate)).title, '⚠️ 未经 AI 审核 · GateA');
check('降级闸(实时) status', tl.wsEventToNode(ev('gate_complete', degradedGate)).status, 'warning');
check('正常闸(实时，零变化)', tl.wsEventToNode(ev('gate_complete', cleanGate)).title, '✅ 审核放行 · GateA');
check('正常闸(实时) status', tl.wsEventToNode(ev('gate_complete', cleanGate)).status, 'success');
check('新字段 review_status 直采', tl.wsEventToNode(ev('gate_complete',
  { ...cleanGate, review_status: 'degraded_unavailable' })).title, '⚠️ 未经 AI 审核 · GateA');

console.log('\n=== ③ 快照路径 gateReviewToNode：同一份历史数据 ===');
const gRev = (over) => ({ decision: 'advance', reason: 'r', eval_score: null, problem_points: [], gate: 'GateC', round: 1,
  timestamp: '2026-09-19T14:00:00', ...over });
check('历史降级(无 review_status，reason 含降级放行)', tl.gateReviewToNode(gRev({
  reason: '审核 LLM 不可用（降级放行）：代码硬校验已通过，未见业务致命问题', eval_score: 0.85 })).title,
  '⚠️ 未经 AI 审核 · GateC');
check('历史降级 status', tl.gateReviewToNode(gRev({
  reason: '审核 LLM 不可用（降级放行）：…', eval_score: 0.85 })).status, 'warning');
check('历史正常(零变化)', tl.gateReviewToNode(gRev({ reason: '结论均有来源支持', eval_score: 0.95 })).title,
  '✅ 审核放行 · GateC');
check('历史正常 status', tl.gateReviewToNode(gRev({ reason: '结论均有来源支持', eval_score: 0.95 })).status, 'success');
check('新数据 review_status 直采', tl.gateReviewToNode(gRev({
  review_status: 'degraded_unavailable', reason: 'x' })).title, '⚠️ 未经 AI 审核 · GateC');

console.log('\n=== ④ 快照路径 engineEventToNode：4 种标题全非空 ===');
for (const [t, p] of [['agent_output_unusable', { agent: 'A', reason: 'r', round: 1 }],
  ['writer_citation_regenerated', { round: 1 }],
  ['gate_llm_unavailable_degraded_advance', { gate: 'GateB', round: 1 }],
  ['researcher_records_synthesized', { agent: 'Researcher', count: 51, round: 1 }]]) {
  const n = tl.engineEventToNode({ event: t, timestamp: '2026-09-19T15:00:00', ...p });
  check(`engineEventToNode[${t}] 标题非空`, n.title && n.title.length > 0 && n.title !== 'undefined', true);
}

console.log(`\n===== PASS ${pass} / FAIL ${fail} =====`);
process.exit(fail ? 1 : 0);
