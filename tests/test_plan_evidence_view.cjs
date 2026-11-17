const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const {readFileSync} = require("node:fs");
const {join} = require("node:path");
const source = readFileSync(join(__dirname, "..", "static", "plan-evidence.js"), "utf8");
const planId = "23832b15-693f-4ff8-a563-4a60fa7d42be";
const ref = (kind, id) => ({kind, id, version: 1});

function sample() {
    return {
        plan_id: planId, plan_version: 1, rule_version: "plan-evidence-v1",
        checked_at: "2026-11-05T10:00:00Z", due: true,
        coverage: [
            {location_id: "A", execution_ids: ["E1", "E2"], inspection_ids: ["I1"]},
            {location_id: "B", execution_ids: [], inspection_ids: []},
        ],
        findings: [
            {code: "missing_execution", location_id: "B", message: "未查到明确执行记录", evidence: [ref("plan", planId)]},
            {code: "unknown_inspection_time", location_id: null, message: "观察时间待核实", evidence: [ref("inspection", "I2")]},
        ],
        comparisons: [
            {location_id: "A", execution_id: "E1", inspection_id: "I1", finding: "issue_observed", relation: "after"},
            {location_id: "A", execution_id: "E2", inspection_id: "I1", finding: "issue_observed", relation: "unknown"},
        ],
        evidence: [ref("plan", planId), ref("execution", "E1"), ref("execution", "E2"),
            ref("inspection", "I1"), ref("inspection", "I2")],
    };
}

class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.handlers = {}; this._text = ""; }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(item => item.textContent).join(" "); }
    set innerHTML(value) { throw Error("不得将证据拼接成HTML"); }
    replaceChildren(...items) { this._text = ""; this.children = items; }
    append(...items) { this.children.push(...items); }
    addEventListener(type, handler) { this.handlers[type] = handler; }
    async emit(type) { return this.handlers[type]?.(); }
    focus() { this.focused = true; }
    scrollIntoView() { this.scrolled = true; }
}

function response(status, body) {
    return {status, ok: status >= 200 && status < 300, json: async () => body};
}

function page({data = sample(), evidence, catalog, record} = {}) {
    const nodes = new Map();
    const node = selector => {
        if (!nodes.has(selector)) nodes.set(selector, new Element());
        return nodes.get(selector);
    };
    node("#plan-evidence").dataset = {planId};
    node("#evidence-result").hidden = true;
    const calls = [];
    const ready = vm.runInNewContext(source, {
        AbortController, setTimeout, clearTimeout,
        document: {querySelector: node, createElement: tag => new Element(tag)},
        async fetch(path, options) {
            calls.push({path, options});
            if (path === "/api/locations") {
                return catalog ? catalog() : response(200, [
                    {id: "A", display_path: "合成小区 / A区 / A区大堂"},
                    {id: "B", display_path: "合成小区 / B区 / B区大堂"},
                ]);
            }
            if (path !== `/api/plans/${planId}/evidence-check`) {
                assert.ok(record, "No record fetch expected before opening a source");
                return record(path, options);
            }
            assert.equal(path, `/api/plans/${planId}/evidence-check`);
            return evidence ? evidence() : response(200, data);
        },
    });
    return {node, calls, ready, refresh: () => node("#refresh-evidence").emit("click")};
}

test("shows coverage, Chinese comparisons and all evidence including unknown scope", async () => {
    const p = page(); await p.ready;
    assert.equal(p.node("#evidence-result").hidden, false);
    const coverage = p.node("#evidence-coverage").textContent;
    assert.match(coverage, /A区大堂/); assert.match(coverage, /2条/);
    assert.match(coverage, /0条明确记录/); assert.doesNotMatch(coverage, /I2/);
    assert.match(p.node("#evidence-references").textContent, /现场检查/);
    assert.ok(p.node("#evidence-references").children.some(x => x.id.endsWith("inspection-I2")));
    assert.equal(p.node("#evidence-references").children.length, 5);
    assert.match(p.node("#evidence-findings").textContent, /缺少执行记录/);
    assert.match(p.node("#evidence-summary").textContent, /已到达计划截止时间/);
    assert.match(p.node("#evidence-comparisons").textContent, /晚于执行人员填报的完成时间/);
    assert.match(p.node("#evidence-comparisons").textContent, /时间信息不足/);
    assert.ok(p.calls.every(call => call.options.cache === "no-store" && !call.options.method));
});

test("failed refresh hides previous result and a later refresh recovers", async () => {
    let status = 200;
    const p = page({evidence: () => response(status, sample())}); await p.ready;
    status = 503; await p.refresh();
    assert.equal(p.node("#evidence-result").hidden, true);
    assert.match(p.node("#evidence-message").textContent, /未完成/);
    assert.equal(p.node("#refresh-evidence").disabled, false);
    status = 200; await p.refresh();
    assert.equal(p.node("#evidence-result").hidden, false);
});

test("network failure is not presented as empty evidence", async () => {
    const p = page({evidence: () => { throw new TypeError("offline"); }}); await p.ready;
    assert.equal(p.node("#evidence-result").hidden, true);
    assert.match(p.node("#evidence-message").textContent, /未完成/);
    assert.equal(p.node("#refresh-evidence").disabled, false);
});

test("timeout gives retry guidance", async () => {
    const p = page({evidence: () => { const error = Error("timeout"); error.name = "AbortError"; throw error; }});
    await p.ready;
    assert.match(p.node("#evidence-message").textContent, /超时/);
    assert.equal(p.node("#evidence-result").hidden, true);
});

test("missing plan is identified", async () => {
    const p = page({evidence: () => response(404)}); await p.ready;
    assert.match(p.node("#evidence-message").textContent, /计划不存在/);
    assert.equal(p.node("#evidence-result").hidden, true);
});

test("wrong plan, invalid arrays and missing plan reference are rejected", async () => {
    for (const change of [data => { data.plan_id = "other"; },
        data => { data.coverage = null; }, data => { data.evidence = []; }]) {
        const data = sample(); change(data);
        const p = page({data}); await p.ready;
        assert.equal(p.node("#evidence-result").hidden, true);
        assert.match(p.node("#evidence-message").textContent, /未完成/);
    }
});

test("catalog failure preserves result with original location IDs", async () => {
    const p = page({catalog: () => { throw Error("catalog offline"); }}); await p.ready;
    assert.equal(p.node("#evidence-result").hidden, false);
    assert.match(p.node("#evidence-coverage").textContent, /A（地点名称暂无法读取）/);
    assert.match(p.node("#evidence-message").textContent, /已显示原地点编号/);
});

test("missing catalog entry preserves the evidence point", async () => {
    const p = page({catalog: () => response(200, [])}); await p.ready;
    assert.match(p.node("#evidence-coverage").textContent, /B（当前台账未查到）/);
    assert.equal(p.node("#evidence-result").hidden, false);
});

test("evidence messages are rendered as text", async () => {
    const data = sample(); data.findings[0].message = '<img src=x onerror="alert(1)">';
    const p = page({data}); await p.ready;
    assert.equal(p.node("#evidence-result").hidden, false);
    assert.match(p.node("#evidence-findings").textContent, /<img src=x/);
});

test("refresh while busy cannot start another query", async () => {
    let release;
    const p = page({evidence: () => new Promise(resolve => { release = resolve; })});
    assert.equal(p.node("#refresh-evidence").disabled, true);
    await p.refresh(); assert.equal(p.calls.length, 1);
    release(response(200, sample())); await p.ready;
    assert.equal(p.node("#refresh-evidence").disabled, false);
});

test("all time relations have distinct Chinese explanations", async () => {
    const data = sample();
    data.comparisons = ["before", "same_time", "after", "unknown"].map(relation => ({
        ...data.comparisons[0], relation,
    }));
    const p = page({data}); await p.ready;
    const text = p.node("#evidence-comparisons").textContent;
    for (const phrase of ["早于", "两个时间相同", "晚于", "时间信息不足"]) assert.match(text, new RegExp(phrase));
});

test("empty findings and comparisons do not declare fulfillment", async () => {
    const data = sample(); data.due = false; data.findings = []; data.comparisons = [];
    const p = page({data}); await p.ready;
    assert.match(p.node("#evidence-summary").textContent, /尚未到达/);
    assert.match(p.node("#evidence-findings").textContent, /仍需人员复核/);
    assert.match(p.node("#evidence-comparisons").textContent, /暂无可进行时间比较/);
});
const flatten = element => [element, ...element.children.flatMap(flatten)];
const linksIn = element => flatten(element).filter(element => element.tag === "a");
function original(kind, id, changes = {}) {
    const base = {id, request_id: "original-request", version: 1, plan_id: planId,
        location_ids: ["A"], location_id: "A", requirements: "仅检查冷水，原因未知。", description: "仅本次、本地点正常。",
        title: "双地点供水检查", owner_name: "陈浩", executor_name: "陈浩", inspector_name: "许岚",
        completion_claim: "completed", finding: "no_issue_observed", planned_start_at: "2026-10-13T01:30:00Z",
        planned_end_at: "2026-10-13T02:30:00Z", claimed_started_at: "2026-10-13T01:35:00Z",
        claimed_finished_at: "2026-10-13T01:45:00Z", observed_at: "2026-10-13T01:50:00Z", recorded_at: "2026-11-15T08:00:00Z"};
    return {...base, ...changes};
}
function recordKind(path) { return path.includes('/inspections/') ? 'inspection' : path.includes('/executions/') ? 'execution' : 'plan'; }

test("observation meaning is mapped by both finding and time; normal or unable-to-verify never implies recurrence", async () => {
    for (const finding of ['no_issue_observed', 'issue_observed', 'unable_to_verify']) {
        for (const relation of ['before', 'same_time', 'after', 'unknown']) {
            const data = sample(); data.comparisons = [{...data.comparisons[0], finding, relation}];
            const p = page({data}); await p.ready;
            const text = p.node('#evidence-comparisons').textContent;
            assert.equal(/再次出现|持续存在|复发/.test(text), finding === 'issue_observed' && relation === 'after');
            if (finding === 'no_issue_observed') { assert.match(text, /本次该地点观察未发现异常/); assert.match(text, /不代表其他地点或之后/); }
            if (finding === 'unable_to_verify') assert.match(text, /不能据此判断该地点是否正常/);
        }
    }
});
test("readable citation opens, scrolls to and highlights its original and offers return to its own citation", async () => {
    const calls = [];
    const p = page({record: path => { calls.push(path); return response(200, original('inspection', 'I1')); }}); await p.ready;
    assert.equal(calls.length, 0);
    const link = linksIn(p.node('#evidence-comparisons')).find(x => x.href.endsWith('inspection-I1'));
    assert.match(link.textContent, /^现场检查｜A区大堂/);
    assert.equal(link.textContent.includes('I1'), false);
    await link.emit('click');
    const source = p.node('#evidence-references').children.find(x => x.id.endsWith('inspection-I1'));
    assert.equal(p.node('#evidence-source-details').open, true); assert.equal(source.open, true);
    assert.equal(source.scrolled, true); assert.equal(source.focused, true); assert.match(source.className, /record-focused/);
    assert.deepEqual(calls, ['/api/inspections/I1']);
    assert.match(source.textContent, /仅本次、本地点正常/);
    assert.match(link.textContent, /2026.*10.*13.*09:50/);
    const metadata = source.children[1].children.find(x => x.tag === 'details');
    assert.ok(metadata); assert.notEqual(metadata.open, true);
    assert.match(metadata.textContent, /合成小区 \/ A区 \/ A区大堂.*A/);
    assert.match(metadata.textContent, /记录编号 I1 记录版本 1/);
    const back = source.children.at(-1); assert.equal(back.hidden, false); assert.equal(back.href, '#' + link.id);
    await link.emit('click'); assert.equal(calls.length, 1); // This rendered check keeps the loaded original.
    assert.ok(p.calls.every(call => call.options.cache === 'no-store' && !call.options.method));
});
test("plan label opens the exact plan and counts distinguish kinds", async () => {
    const p = page({record: path => { assert.equal(path, '/api/plans/' + planId); return response(200, original('plan', planId)); }}); await p.ready;
    await linksIn(p.node('#evidence-current-plan'))[0].emit('click');
    assert.match(p.node('#evidence-current-plan').textContent, /双地点供水检查/);
    assert.match(p.node('#evidence-references').textContent, /仅检查冷水，原因未知/);
    assert.match(p.node('#evidence-summary').textContent, /计划1份、执行记录2条、现场检查2条/);
});
test("wrong version, wrong record or wrong plan never shows a different original", async () => {
    for (const changes of [{version: 2}, {id: 'I2'}, {plan_id: 'other-plan'}]) {
        const p = page({record: () => response(200, original('inspection', 'I1', {...changes, description: '不应显示的新原文'}))}); await p.ready;
        await linksIn(p.node('#evidence-comparisons')).find(x => x.href.endsWith('inspection-I1')).emit('click');
        const source = p.node('#evidence-references').children.find(x => x.id.endsWith('inspection-I1'));
        assert.match(source.textContent, /不一致.*未展示原文/); assert.equal(source.textContent.includes('不应显示的新原文'), false);
        assert.equal(p.node('#evidence-result').hidden, false);
    }
});
test("source failures preserve check result and allow an explicit retry without claiming empty evidence", async () => {
    for (const status of [404, 503]) {
        let ok = false;
        const p = page({record: () => response(ok ? 200 : status, original('inspection', 'I1'))}); await p.ready;
        const link = linksIn(p.node('#evidence-comparisons')).find(x => x.href.endsWith('inspection-I1'));
        await link.emit('click');
        const source = p.node('#evidence-references').children.find(x => x.id.endsWith('inspection-I1'));
        assert.match(source.textContent, /未当作空记录|未将失败当作查无记录/);
        assert.equal(p.node('#evidence-result').hidden, false);
        ok = true; await link.emit('click'); assert.match(source.textContent, /仅本次、本地点正常/);
    }
});
test("unknown actual times stay unknown; source descriptions are rendered as text", async () => {
    const p = page({record: () => response(200, original('inspection', 'I1', {observed_at: null, description: '<img src=x onerror=alert(1)>'}))}); await p.ready;
    const link = linksIn(p.node('#evidence-comparisons')).find(x => x.href.endsWith('inspection-I1')); await link.emit('click');
    assert.match(link.textContent, /观察时间（北京时间）：未注明，待核实/); assert.equal(link.textContent.includes('2026'), false);
    const source = p.node('#evidence-references').children.find(x => x.id.endsWith('inspection-I1'));
    assert.ok(flatten(source).some(x => x.tag === 'pre' && x.textContent === '<img src=x onerror=alert(1)>'));
});
test("short place labels keep enough parent context to distinguish names", async () => {
    const p = page({catalog: () => response(200, [{id: 'A', display_path: '小区 / A区 / 大堂'}, {id: 'B', display_path: '小区 / B区 / 大堂'}])}); await p.ready;
    const rows = p.node('#evidence-coverage').children;
    assert.equal(rows[0].children[0]._text, 'A区 / 大堂'); assert.equal(rows[1].children[0]._text, 'B区 / 大堂');
    assert.match(rows[0].children[0].textContent, /小区 \/ A区 \/ 大堂（A）/);
});
test("unknown-scope original remains available without being counted at a known point", async () => {
    const p = page({record: () => response(200, original('inspection', 'I2', {location_id: null, observed_at: null}))}); await p.ready;
    const source = p.node('#evidence-references').children.find(x => x.id.endsWith('inspection-I2'));
    source.open = true; await source.emit('toggle');
    assert.match(source.textContent, /地点待核实/);
    assert.equal(p.node('#evidence-coverage').textContent.includes('I2'), false);
    assert.equal(p.node('#evidence-references').children.length, 5);
});
test("late source response cannot fill a newly refreshed check or its plan link", async () => {
    let release;
    const p = page({record: () => new Promise(resolve => { release = resolve; })}); await p.ready;
    const source = p.node('#evidence-references').children.find(x => x.id.endsWith('inspection-I1'));
    const pending = linksIn(p.node('#evidence-comparisons')).find(x => x.href.endsWith('inspection-I1')).emit('click');
    await p.refresh();
    const newSource = p.node('#evidence-references').children.find(x => x.id.endsWith('inspection-I1'));
    assert.notEqual(source, newSource);
    release(response(200, original('inspection', 'I1', {description: '旧请求返回的内容'}))); await pending;
    assert.equal(newSource.textContent.includes('旧请求返回的内容'), false);
    assert.equal(source.textContent.includes('旧请求返回的内容'), false);
});
test("failed refresh removes previous plan citation instead of leaving a stale active link", async () => {
    let status = 200;
    const p = page({evidence: () => response(status, sample())}); await p.ready;
    assert.equal(linksIn(p.node('#evidence-current-plan')).length, 1);
    status = 503; await p.refresh();
    assert.equal(linksIn(p.node('#evidence-current-plan')).length, 0);
});

test('every original has a fixed material type before reading and after loading a plan titled as an inspection', async () => {
    const p = page({record: path => response(200, original(recordKind(path), path.split('/').at(-1), {title: '走廊及302室供水检查'}))}); await p.ready;
    const sources = p.node('#evidence-references').children;
    assert.deepEqual(sources.map(source => source.children[0].children[0].textContent), ['计划要求', '执行填报', '执行填报', '现场检查', '现场检查']);
    assert.equal(p.calls.length, 2); // Reading details is still on demand.
    const plan = sources[0]; plan.open = true; await plan.emit('toggle'); plan.open = false;
    assert.equal(plan.children[0].children[0].textContent, '计划要求');
    assert.match(plan.children[0].textContent, /走廊及302室供水检查/);
    assert.match(plan.children[0].textContent, /计划时间（北京时间）.*09:30.*10:30.*负责人：陈浩/);
    assert.equal(plan.textContent.includes('发布人'), false);
    const execution = sources[1]; execution.open = true; await execution.emit('toggle');
    assert.equal(execution.children[0].children[0].textContent, '执行填报');
    assert.match(execution.textContent, /本次填报工作地点/);
    const inspection = sources[3]; inspection.open = true; await inspection.emit('toggle');
    assert.equal(inspection.children[0].children[0].textContent, '现场检查');
    assert.match(inspection.textContent, /本次观察地点/);
    assert.match(plan.textContent, /计划覆盖地点/);
});

test('all loaded type titles show actual dates and Beijing timezone, including a plan spanning midnight', async () => {
    const p = page({record: path => response(200, original(recordKind(path), path.split('/').at(-1), {
        planned_start_at: '2026-10-13T15:30:00Z', planned_end_at: '2026-10-13T16:30:00Z',
        claimed_finished_at: '2026-10-13T16:45:00Z', observed_at: '2026-10-13T16:50:00Z',
    }))}); await p.ready;
    const sources = p.node('#evidence-references').children;
    for (const index of [0, 1, 3]) { sources[index].open = true; await sources[index].emit('toggle'); }
    const plan = sources[0].children[0].textContent;
    assert.match(plan, /2026\/10\/13 23:30.*2026\/10\/14 00:30/);
    assert.match(plan, /北京时间/);
    assert.match(sources[1].children[0].textContent, /执行填报.*填报完成时间（北京时间）：2026\/10\/14 00:45.*执行人：陈浩/);
    assert.match(sources[3].children[0].textContent, /现场检查.*观察时间（北京时间）：2026\/10\/14 00:50.*检查人：许岚/);
});

test('unknown completion and observation times remain unknown in typed titles and never use recording time', async () => {
    const p = page({record: path => response(200, original(recordKind(path), path.split('/').at(-1), {claimed_finished_at: null, observed_at: null}))}); await p.ready;
    for (const ending of ['execution-E1', 'inspection-I1']) {
        const source = p.node('#evidence-references').children.find(x => x.id.endsWith(ending));
        source.open = true; await source.emit('toggle');
        const title = source.children[0].textContent;
        assert.match(title, /北京时间.*未注明，待核实/);
        assert.equal(title.includes('2026'), false);
    }
});
function twoPlaceCheck() {
    const data = sample();
    data.evidence = [ref('plan', planId), ref('execution', 'E1'), ref('inspection', 'I1'), ref('inspection', 'I2')];
    data.coverage = [
        {location_id: 'A', execution_ids: ['E1'], inspection_ids: ['I1']},
        {location_id: 'B', execution_ids: [], inspection_ids: ['I2']},
    ];
    data.comparisons = [data.comparisons[0]];
    data.findings = [{code: 'missing_execution', location_id: 'B', message: '原规则提示：先核实是否漏填，再决定现场核查。', evidence: [ref('plan', planId)]}];
    return data;
}
test('missing execution separates point query counts from the plan requirement without treating inspection as execution', async () => {
    const data = twoPlaceCheck(), before = JSON.stringify(data);
    const p = page({data, record: path => response(200, original('plan', planId))}); await p.ready;
    const item = p.node('#evidence-findings').children[0];
    const paragraphs = item.children.filter(x => x.tag === 'p').map(x => x._text).join(' ');
    assert.match(paragraphs, /本次查询结果：B区大堂，执行记录0条、现场检查记录1条/);
    assert.ok(item.children.some(x => x.tag === "p" && x.textContent === '本次核对未查到该地点的执行记录。请核实是否已开展工作、是否遗漏填报，再决定是否需要现场核查。'));
    assert.match(paragraphs, /已有现场检查可供对照，但不能代替执行填报/);
    assert.doesNotMatch(paragraphs, /需要重新检查|应重新检查/);
    assert.ok(paragraphs.indexOf("请核实") < paragraphs.indexOf("再决定"));
    const requirement = item.children.find(x => x.className === 'record-citations');
    assert.equal(requirement.children[0].textContent, '对照计划要求：');
    assert.equal(linksIn(requirement).length, 1);
    assert.ok(linksIn(requirement)[0].href.endsWith('plan-' + planId));
    await linksIn(requirement)[0].emit('click');
    assert.equal(p.node('#evidence-references').children[0].open, true);
    assert.match(p.node('#evidence-references').children[0].textContent, /计划要求原文/);
    assert.equal(linksIn(item).find(x => x.textContent === '查看各地点记录数量').href, '#evidence-coverage-title');
    assert.equal(p.node('#evidence-coverage').children.length, 2);
    assert.match(p.node('#evidence-coverage').children[0].children[1].textContent, /^1条/);
    assert.match(p.node('#evidence-coverage').children[1].children[1].textContent, /^0条明确记录/);
    assert.equal(JSON.stringify(data), before);
});
test('missing inspection shows its own query counts and does not turn execution into observation evidence', async () => {
    const data = twoPlaceCheck(); data.coverage[0].inspection_ids = [];
    data.findings = [{code: 'missing_inspection', location_id: 'A', message: '原提示', evidence: [ref('plan', planId)]}];
    data.comparisons = [];
    const p = page({data}); await p.ready;
    const item = p.node('#evidence-findings').children[0];
    const paragraphs = item.children.filter(x => x.tag === 'p').map(x => x._text).join(' ');
    assert.match(paragraphs, /A区大堂，执行记录1条、现场检查记录0条/);
    assert.match(paragraphs, /是否已进行现场检查.*是否遗漏检查填报/);
    assert.equal(item.children.find(x => x.className === 'record-citations').children[0].textContent, '对照计划要求：');
});
test('missing-record display refuses inconsistent quantities or a reference that is not the selected plan', async () => {
    for (const change of [
        data => { data.coverage[1].execution_ids = ['E1']; },
        data => { data.coverage = data.coverage.slice(0, 1); },
        data => { data.findings[0].evidence = [ref('inspection', 'I2')]; },
    ]) {
        const data = twoPlaceCheck(); change(data);
        const p = page({data}); await p.ready;
        assert.equal(p.node('#evidence-result').hidden, true);
        assert.match(p.node('#evidence-message').textContent, /核对未完成/);
    }
});
test('unknown-scope execution stays outside point counts while the typed original remains readable', async () => {
    const data = twoPlaceCheck(); data.evidence.push(ref('execution', 'E2'));
    const p = page({data, record: () => response(200, original('execution', 'E2', {location_ids: []}))}); await p.ready;
    const source = p.node('#evidence-references').children.find(x => x.id.endsWith('execution-E2'));
    source.open = true; await source.emit('toggle');
    assert.match(source.children[0].textContent, /执行填报.*地点待核实/);
    const item = p.node('#evidence-findings').children[0];
    assert.match(item.textContent, /B区大堂，执行记录0条、现场检查记录1条/);
    assert.match(item.textContent, /是否遗漏填报/);
});
