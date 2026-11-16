const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const {readFileSync} = require("node:fs");
const {join} = require("node:path");
const source = readFileSync(join(__dirname, "..", "static", "report-evidence.js"), "utf8").replace(/^export /gm, "");
const reportId = "565eddc7-b40e-47b1-805f-cd89f8128380";
const time = "2026-11-01T01:40:00Z";

function sample() {
    return {
        rule_version: "report-evidence-v1", checked_at: time,
        report: {id: reportId, service_category: "cleaning", location_id: "A",
            observed_at: time, received_at: time, description: " 合成居民原文 "},
        links: [
            {id: "L1", report_id: reportId, plan_id: "P1", status: "active", version: 1,
                linked_by: "合成关联人", linked_at: time, reason: "原关联理由"},
            {id: "L0", report_id: reportId, plan_id: "P1", status: "revoked", version: 2,
                linked_by: "合成关联人", linked_at: time, reason: "旧关联理由",
                revoked_by: "合成撤销人", revoked_at: time, revoke_reason: "选错计划"},
        ],
        linked_plans: [{
            link_id: "L1", scope_matches: true,
            plan: {id: "P1", title: "合成保洁计划", owner_name: "合成负责人", location_ids: ["A", "B"],
                planned_start_at: time, planned_end_at: time, requirements: " 原计划要求 "},
            executions: [
                {id: "E1", plan_id: "P1", completion_claim: "completed", location_ids: ["A"],
                    executor_name: "合成人员", claimed_started_at: null, claimed_finished_at: time,
                    recorded_at: time, description: " 原执行说明 "},
                {id: "E2", plan_id: "P1", completion_claim: "unknown", location_ids: [],
                    executor_name: "合成人员", claimed_started_at: null, claimed_finished_at: null,
                    recorded_at: time, description: "执行情况未知"},
            ],
            inspections: [{id: "I1", plan_id: "P1", finding: "unable_to_verify", location_id: null,
                inspector_name: "合成人员", observed_at: null, recorded_at: time, description: "原巡查说明"}],
            report_comparisons: [{execution_id: "E1", relation: "after", message: "观察晚于声称完成，原因仍待核实"}],
            excluded_execution_count: 1, excluded_inspection_count: 0, warnings: ["地点未知的记录已保留"],
        }],
    };
}

class Element {
    constructor() { this.children = []; this.handlers = {}; this._text = ""; }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(x => x.textContent).join(" "); }
    set innerHTML(value) { throw Error("不得将证据拼接成HTML"); }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this._text = ""; this.children = items; }
    addEventListener(type, handler) { this.handlers[type] = handler; }
    async emit(type) { return this.handlers[type]?.(); }
}

const response = (status, body) => ({status, ok: status >= 200 && status < 300, json: async () => body});

function page({data = sample(), evidence, catalog} = {}) {
    const nodes = new Map();
    const node = selector => {
        if (!nodes.has(selector)) nodes.set(selector, new Element());
        return nodes.get(selector);
    };
    node("#report-evidence").dataset = {reportId};
    node("#report-evidence-result").hidden = true;
    const calls = [];
    const ready = vm.runInNewContext(source, {
        AbortController, setTimeout, clearTimeout,
        document: {querySelector: node, createElement: () => new Element()},
        async fetch(path, options) {
            calls.push({path, options});
            if (path === "/api/locations") return catalog ? catalog() : response(200, [
                {id: "A", display_path: "合成小区 / A大堂"}, {id: "B", display_path: "合成小区 / B大堂"},
            ]);
            assert.equal(path, `/api/service-requests/${reportId}/evidence-check`);
            return evidence ? evidence() : response(200, data);
        },
    });
    return {node, calls, ready, refresh: () => node("#refresh-report-evidence").emit("click")};
}

test("shows original evidence, independent times, unknown fields and link history", async () => {
    const p = page(); await p.ready;
    assert.equal(p.node("#report-evidence-result").hidden, false);
    assert.equal(p.node("#joint-report-description").textContent, " 合成居民原文 ");
    assert.equal(p.node("#joint-links").children.length, 2);
    assert.ok(p.node("#joint-plans").children[0].children.some(element =>
        element.href === "/executions?plan_id=P1&report_id=" + reportId));
    assert.match(p.node("#joint-links").textContent, /选错计划/);
    const text = p.node("#joint-plans").textContent;
    for (const phrase of ["A大堂", "B大堂", "原执行说明", "原巡查说明", "观察时间", "录入时间", "未知，待核实", "原因仍待核实"])
        assert.ok(text.includes(phrase), phrase);
    assert.ok(p.calls.every(x => !x.options.method && x.options.cache === "no-store"));
});

test("unlinked state retains report and does not claim resolution", async () => {
    const data = sample(); data.links = []; data.linked_plans = [];
    const p = page({data}); await p.ready;
    assert.equal(p.node("#report-evidence-result").hidden, false);
    assert.match(p.node("#joint-links").textContent, /还没有添加或移除计划/);
    assert.match(p.node("#joint-plans").textContent, /不表示问题已经解决/);
});

test("failed refresh hides stale result and retry recovers", async () => {
    let status = 200;
    const p = page({evidence: () => response(status, sample())}); await p.ready;
    status = 503; await p.refresh();
    assert.equal(p.node("#report-evidence-result").hidden, true);
    assert.match(p.node("#report-evidence-message").textContent, /未完成/);
    status = 200; await p.refresh();
    assert.equal(p.node("#report-evidence-result").hidden, false);
});

test("wrong context, omitted active evidence and incomplete comparisons are rejected", async () => {
    for (const change of [x => {x.report.id = "other";}, x => {x.linked_plans = [];},
        x => {x.linked_plans[0].link_id = "L0";}, x => {x.linked_plans[0].report_comparisons = [];},
        x => {x.linked_plans[0].inspections[0].plan_id = "other";}]) {
        const data = sample(); change(data);
        const p = page({data}); await p.ready;
        assert.equal(p.node("#report-evidence-result").hidden, true);
    }
});

test("original descriptions are rendered as text", async () => {
    const data = sample(); data.linked_plans[0].executions[0].description = '<img src=x onerror="alert(1)">';
    const p = page({data}); await p.ready;
    assert.equal(p.node("#report-evidence-result").hidden, false);
    assert.match(p.node("#joint-plans").textContent, /<img src=x/);
});

test("catalog failure preserves IDs and evidence", async () => {
    const p = page({catalog: () => {throw Error("catalog offline");}}); await p.ready;
    assert.equal(p.node("#report-evidence-result").hidden, false);
    assert.match(p.node("#joint-report-fields").textContent, /A（地点名称暂无法读取）/);
});

test("network failure, missing report and timeout retain retry guidance", async () => {
    for (const evidence of [() => {throw TypeError("offline");}, () => response(404),
        () => {const error = Error("timeout"); error.name = "AbortError"; throw error;}]) {
        const p = page({evidence}); await p.ready;
        assert.equal(p.node("#report-evidence-result").hidden, true);
        assert.equal(p.node("#refresh-report-evidence").disabled, false);
        assert.match(p.node("#report-evidence-message").textContent, /未完成|不存在|超时/);
    }
});

test("refresh while busy does not start a duplicate read", async () => {
    let release;
    const p = page({evidence: () => new Promise(resolve => {release = resolve;})});
    await p.refresh(); assert.equal(p.calls.length, 1);
    release(response(200, sample())); await p.ready;
    assert.equal(p.node("#report-evidence-result").hidden, false);
});

test("明确匹配与地点未知分别计数，保留排除数量", async () => {
    const data = sample();
    data.linked_plans[0].inspections.push({
        ...data.linked_plans[0].inspections[0], id: "I2", location_id: "A",
    });
    const p = page({data}); await p.ready;
    const text = p.node("#joint-plans").textContent;
    assert.match(text, /工作执行记录\s+本报事地点的记录：1 条；未注明地点的记录：1 条。/);
    assert.match(text, /现场检查记录\s+本报事地点的记录：1 条；未注明地点的记录：1 条。/);
    assert.match(text, /其他地点的记录未显示在这里：执行 1 条，现场检查 0 条/);
});

test("同一执行记录覆盖多个地点仍只计一条", async () => {
    const data = sample();
    data.linked_plans[0].executions[0].location_ids = ["A", "B"];
    const p = page({data}); await p.ready;
    assert.match(p.node("#joint-plans").textContent,
        /工作执行记录\s+本报事地点的记录：1 条；未注明地点的记录：1 条。/);
});

test("范围不匹配时先警告，再展示计划上下文", async () => {
    const data = sample();
    const item = data.linked_plans[0];
    item.scope_matches = false;
    item.report_comparisons[0] = {
        execution_id: "E1", relation: "unknown", message: "范围待核实。",
    };
    const p = page({data}); await p.ready;
    const article = p.node("#joint-plans").children[0];
    assert.equal(article.children[1].className, "scope-warning");
    assert.match(article.textContent, /暂不比较时间先后/);
    assert.match(article.textContent, /工作执行记录\s+该计划共有 2 条记录，其中 1 条未注明地点。/);
    assert.doesNotMatch(article.textContent, /本报事地点的记录：/);
    assert.doesNotMatch(article.textContent, /其他地点的记录未显示在这里/);
    assert.match(article.textContent, /原执行说明/);
});

test("报事地点未知时不把未知巡查算成明确匹配", async () => {
    const data = sample();
    data.report.location_id = null;
    data.linked_plans[0].scope_matches = false;
    data.linked_plans[0].report_comparisons[0] = {
        execution_id: "E1", relation: "unknown", message: "地点待核实。",
    };
    const p = page({data}); await p.ready;
    assert.equal(p.node("#report-evidence-result").hidden, false);
    assert.match(p.node("#joint-plans").textContent,
        /现场检查记录\s+该计划共有 1 条记录，其中 1 条未注明地点。/);
    assert.doesNotMatch(p.node("#joint-plans").textContent, /本报事地点的记录：/);
});

test("多份计划的计数和排除记录分别展示", async () => {
    const data = sample();
    const second = JSON.parse(JSON.stringify(data.linked_plans[0]));
    second.link_id = "L2";
    second.plan.id = "P2";
    second.plan.title = "第二份合成计划";
    second.executions = [];
    second.report_comparisons = [];
    second.inspections[0] = {
        ...second.inspections[0], id: "I2", plan_id: "P2", location_id: "A",
    };
    second.excluded_execution_count = 5;
    second.excluded_inspection_count = 6;
    data.links.push({...data.links[0], id: "L2", plan_id: "P2"});
    data.linked_plans.push(second);
    const p = page({data}); await p.ready;
    const articles = p.node("#joint-plans").children;
    assert.equal(articles.length, 2);
    assert.match(articles[0].textContent, /计划 1：合成保洁计划/);
    assert.match(articles[0].textContent, /执行 1 条，现场检查 0 条/);
    assert.match(articles[1].textContent, /计划 2：第二份合成计划/);
    assert.match(articles[1].textContent,
        /现场检查记录\s+本报事地点的记录：1 条；未注明地点的记录：0 条。/);
    assert.match(articles[1].textContent, /执行 5 条，现场检查 6 条/);
});

test("只有未知记录时明确匹配数为零，原记录仍保留", async () => {
    const data = sample();
    data.linked_plans[0].executions.shift();
    data.linked_plans[0].report_comparisons = [];
    const p = page({data}); await p.ready;
    const text = p.node("#joint-plans").textContent;
    assert.match(text, /工作执行记录\s+本报事地点的记录：0 条；未注明地点的记录：1 条。/);
    assert.match(text, /现场检查记录\s+本报事地点的记录：0 条；未注明地点的记录：1 条。/);
    assert.match(text, /执行情况未知/);
    assert.match(text, /原巡查说明/);
});

const actionSource = readFileSync(join(__dirname, "..", "static", "report-link-actions.js"), "utf8")
    .replace(/^import .*;\s*/m, "");
const actionPlanId = "00000000-0000-4000-8000-000000000001";
const actionLinkId = "00000000-0000-4000-8000-000000000002";
const actionKey = "property-v2-link-action-v1:" + reportId;
const actionPlan = {id: actionPlanId, title: "合成保洁计划", service_category: "cleaning",
    planned_start_at: time, requirements: "分别核对大堂", location_ids: ["A"]};
const actionData = () => ({report: {id: reportId}, links: [{id: actionLinkId, plan_id: actionPlanId,
    report_id: reportId, status: "active", version: 1, reason: "原关联理由"}]});

function actionReceipt(path, payload) {
    const common = {id: actionLinkId, report_id: reportId, plan_id: actionPlanId, version: 1};
    return path.endsWith("/revoke")
        ? {...common, request_id: "original-create-request", status: "revoked", version: payload.expected_version + 1,
            revocation_request_id: payload.request_id, revoked_by: payload.revoked_by, revoke_reason: payload.reason}
        : {...common, request_id: payload.request_id, plan_id: payload.plan_id, status: "active",
            linked_by: payload.linked_by, reason: payload.reason};
}

function actionPage({store = new Map(), initial = actionData(), post, catalog, failStore} = {}) {
    const nodes = new Map();
    const node = key => {
        if (!nodes.has(key)) {const item = new Element(); item.value = ""; nodes.set(key, item);}
        return nodes.get(key);
    };
    const field = name => node("field:" + name);
    field("kind").value = "link";
    const form = node("#link-action-form");
    form.elements = {namedItem: field};
    form.reportValidity = () => !!field("actor").value && !!field("reason").value
        && !!field(field("kind").value === "link" ? "plan_id" : "link_id").value;
    form.reset = () => {for (const name of ["kind", "plan_id", "link_id", "actor", "reason"]) field(name).value = name === "kind" ? "link" : "";};
    node("#report-evidence").dataset = {reportId};
    const calls = []; let notify, reloads = 0;
    const ready = vm.runInNewContext(actionSource, {
        AbortController, setTimeout, clearTimeout, crypto: require("node:crypto"),
        document: {querySelector: node, createElement: () => new Element()},
        window: {location: {reload() {reloads += 1;}}},
        sessionStorage: {getItem: key => store.get(key) ?? null, removeItem: key => store.delete(key),
            setItem(key, value) {if (failStore?.(JSON.parse(value))) throw Error("storage unavailable"); store.set(key, value);}},
        watchEvidence(callback) {notify = callback; callback(initial, new Map([["A", "合成小区 / A大堂"]]));},
        async fetch(path, options) {
            calls.push({path, options});
            if (!options.method) return catalog ? catalog(path) : response(200, [actionPlan]);
            const payload = JSON.parse(options.body);
            return post ? post(path, payload) : response(path.endsWith("/revoke") ? 200 : 201, actionReceipt(path, payload));
        },
    });
    return {node, field, store, ready, calls, get reloads() {return reloads;},
        posts: () => calls.filter(x => x.options.method === "POST"),
        publish: data => notify(data),
        fill(kind = "link", reason = " 合成操作理由 ") {
            field("kind").value = kind; field("plan_id").value = actionPlanId; field("link_id").value = actionLinkId;
            field("actor").value = " 合成人员 "; field("reason").value = reason; form.handlers.change();
        },
        submit: () => form.handlers.submit({preventDefault() {}}),
        newAction: () => node("#new-link-action").emit("click"),
    };
}

function savedAction(page) {return JSON.parse(page.store.get(actionKey));}

test("link and revoke use matching context, preserve raw reasons and display clear receipts", async () => {
    for (const kind of ["link", "revoke"]) {
        const p = actionPage(); await p.ready; p.fill(kind); await p.submit();
        assert.equal(p.posts().length, 1);
        const saved = savedAction(p);
        assert.equal(saved.phase, "accepted"); assert.equal(saved.payload.reason, " 合成操作理由 ");
        assert.equal(p.reloads, 1); assert.equal(p.node("#new-link-action").disabled, true);
        if (kind === "revoke") {
            assert.match(p.posts()[0].path, new RegExp(actionLinkId + "/revoke$"));
            assert.equal(saved.payload.expected_version, 1);
            assert.equal(saved.receipt.revocation_request_id, saved.payload.request_id);
        } else assert.equal(saved.receipt.request_id, saved.payload.request_id);
        assert.match(p.node("#link-action-receipt").textContent, /报事编号（report_id）/);
        assert.match(p.node("#link-action-receipt").textContent, /操作记录编号（id）/);
    }
});

test("422 permits correction with the same submission id for both operations", async () => {
    for (const kind of ["link", "revoke"]) {
        let count = 0;
        const p = actionPage({post: (path, payload) => ++count === 1
            ? response(422, {detail: "不能仅包含空白字符"}) : response(200, actionReceipt(path, payload))});
        await p.ready; p.fill(kind, "   "); await p.submit();
        const first = savedAction(p).payload.request_id;
        assert.equal(savedAction(p).phase, "invalid"); assert.equal(p.node("#link-action-fields").disabled, false);
        assert.equal(p.node("#new-link-action").hidden, false);
        assert.match(p.node("#new-link-action").textContent, /放弃输入/);
        p.field("reason").value = "合成修正理由"; await p.submit();
        assert.equal(savedAction(p).phase, "accepted");
        assert.equal(savedAction(p).payload.request_id, first);
        assert.equal(savedAction(p).payload.reason, "合成修正理由");
    }
});

test("lost receipt then reload retries the exact original operation without catalog or fresh evidence", async () => {
    for (const kind of ["link", "revoke"]) {
        const ledger = new Map(); let first = true;
        const post = (path, payload) => {
            if (!ledger.has(payload.request_id)) ledger.set(payload.request_id, actionReceipt(path, payload));
            if (first) {first = false; throw Error("saved but receipt lost");}
            return response(200, ledger.get(payload.request_id));
        };
        const p = actionPage({post}); await p.ready; p.fill(kind); await p.submit();
        assert.equal(savedAction(p).phase, "pending");
        const original = savedAction(p).payload;
        const restored = actionPage({store: p.store, post, initial: null, catalog: () => {throw Error("offline");}});
        await restored.ready;
        restored.field("reason").value = "不能替换待确认内容";
        await restored.submit();
        assert.deepEqual(JSON.parse(restored.posts()[0].options.body), original);
        assert.equal(ledger.size, 1); assert.equal(savedAction(restored).phase, "accepted");
    }
});

test("409 blocks another write until fresh evidence and explicit new operation", async () => {
    const p = actionPage({post: () => response(409, {detail: "version conflict"})});
    await p.ready; p.fill("revoke"); await p.submit();
    const first = savedAction(p).payload.request_id;
    assert.equal(savedAction(p).phase, "conflict");
    await p.submit(); await p.newAction(); assert.equal(p.posts().length, 1);
    assert.equal(savedAction(p).phase, "conflict");
    const latest = actionData(); latest.links[0].version = 2;
    p.publish(latest); await p.newAction(); p.fill("revoke"); await p.submit();
    assert.notEqual(savedAction(p).payload.request_id, first);
    assert.equal(savedAction(p).payload.expected_version, 2);
});

test("mismatched success receipts remain pending", async () => {
    for (const kind of ["link", "revoke"]) {
        const p = actionPage({post: (path, payload) => response(200,
            {...actionReceipt(path, payload), report_id: "other-report"})});
        await p.ready; p.fill(kind); await p.submit();
        assert.equal(savedAction(p).phase, "pending"); assert.equal(p.reloads, 0);
        assert.match(p.node("#link-action-message").textContent, /暂未确认/);
    }
});

test("storage failure before sending or after receiving never creates a replacement submission", async () => {
    for (const after of [false, true]) {
        const p = actionPage({failStore: value => after ? value.phase === "accepted" : value.phase === "pending"});
        await p.ready; p.fill(); await p.submit();
        assert.equal(p.posts().length, after ? 1 : 0);
        if (after) assert.equal(savedAction(p).phase, "pending");
        else assert.equal(p.store.has(actionKey), false);
    }
});

test("corrupt or cross-report draft blocks writes", async () => {
    const valid = actionPage({post: () => {throw Error("offline");}});
    await valid.ready; valid.fill(); await valid.submit();
    const other = savedAction(valid); other.report_id = "00000000-0000-4000-8000-000000000099";
    for (const stored of ["bad-json", JSON.stringify(other)]) {
        const p = actionPage({store: new Map([[actionKey, stored]])});
        await p.ready; p.fill(); await p.submit(); assert.equal(p.posts().length, 0);
        assert.match(p.node("#link-action-message").textContent, /暂存无法读取/);
    }
});

test("double submit while saving sends once", async () => {
    let release;
    const p = actionPage({post: (path, payload) => new Promise(resolve => {
        release = () => resolve(response(201, actionReceipt(path, payload)));
    })});
    await p.ready; p.fill(); const first = p.submit(); await p.submit();
    assert.equal(p.posts().length, 1); release(); await first;
    assert.equal(savedAction(p).phase, "accepted");
});

test("accepted reload does not POST and new operation clears only this report draft", async () => {
    const p = actionPage({post: (path, payload) => response(200,
        {...actionReceipt(path, payload), status: "revoked", version: 2})});
    await p.ready; p.fill(); await p.submit();
    assert.match(p.node("#link-action-receipt").textContent, /已移除/);
    p.store.set("another-report-draft", "keep");
    const restored = actionPage({store: p.store}); await restored.ready;
    assert.equal(restored.posts().length, 0);
    assert.equal(savedAction(restored).phase, "accepted");
    await restored.newAction();
    assert.equal(p.store.has(actionKey), false); assert.equal(p.store.get("another-report-draft"), "keep");
});

test("plan pagination is complete and catalog failure still permits revoking a known link", async () => {
    const plans = Array.from({length: 101}, (_, i) => ({...actionPlan,
        id: `00000000-0000-4000-8000-${(i + 10).toString(16).padStart(12, "0")}`}));
    const p = actionPage({catalog: path => {
        const offset = Number(new URL(path, "http://test").searchParams.get("offset"));
        return response(200, plans.slice(offset, offset + 100));
    }});
    await p.ready;
    assert.equal(p.field("plan_id").children.length, 102);
    assert.equal(p.calls.filter(x => !x.options.method).length, 2);
    const offline = actionPage({catalog: () => response(503, {})}); await offline.ready;
    offline.fill(); await offline.submit(); assert.equal(offline.posts().length, 0);
    offline.fill("revoke"); await offline.submit(); assert.equal(offline.posts().length, 1);
    assert.equal(savedAction(offline).phase, "accepted");
});
