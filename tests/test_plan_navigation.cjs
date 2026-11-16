const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const intakeSource = readFileSync(join(__dirname, '..', 'static', 'plans.js'), 'utf8');
const listSource = readFileSync(join(__dirname, '..', 'static', 'plan-list.js'), 'utf8');
const key = 'property-v2-plan-intake-v1';
const planId = '23832b15-693f-4ff8-a563-4a60fa7d42be';
class Element {
    constructor() { this.children = []; this.handlers = {}; this.style = {}; this.value = ''; this._text = ''; this.open = false; }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(' '); }
    set innerHTML(value) { throw Error('Use text, not HTML, for saved content'); }
    get options() { return this.children; }
    get selectedOptions() { return this.children.filter(option => option.selected); }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this._text = ''; this.children = children; }
    addEventListener(type, callback) { this.handlers[type] = callback; }
    async emit(type) { return this.handlers[type]?.({preventDefault() {}}); }
    focus() { this.focused = true; }
    scrollIntoView() {}
}
function response(status, body) { return {status, ok: status >= 200 && status < 300, json: async () => body}; }
const tick = () => new Promise(resolve => setImmediate(resolve));
const payload = () => ({request_id: 'request-original', title: '双地点检查', service_category: 'water_supply',
    plan_kind: 'special', requirements: '仅检查两处冷水，原因未知。', owner_name: '陈浩',
    location_ids: ['ROOM', 'CORRIDOR'], planned_start_at: '2026-10-13T09:30+08:00', planned_end_at: '2026-10-13T10:30+08:00'});
const receipt = p => ({...p, id: planId, created_at: '2026-11-16T06:00:00Z', version: 1});
async function intake({storage = new Map(), post, failRead = false} = {}) {
    const nodes = new Map();
    const node = selector => { if (!nodes.has(selector)) nodes.set(selector, new Element()); return nodes.get(selector); };
    const fields = Object.fromEntries(['title', 'service_category', 'plan_kind', 'requirements', 'owner_name',
        'location_ids', 'planned_start_at', 'planned_end_at'].map(name => [name, new Element()]));
    const form = node('#plan-form');
    form.elements = {namedItem: name => fields[name]};
    form.reportValidity = () => true;
    let resets = 0;
    form.reset = () => { resets++; for (const field of Object.values(fields)) field.value = ''; };
    const posts = [];
    vm.runInNewContext(intakeSource, {AbortController, setTimeout, clearTimeout,
        crypto: {randomUUID: () => 'request-new'},
        document: {querySelector: node, createElement: () => new Element()},
        sessionStorage: {getItem(k) { if (failRead) throw Error('storage'); return storage.get(k) ?? null; },
            setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k)},
        async fetch(url, options = {}) {
            if (options.method === 'POST') {
                assert.equal(url, '/api/plans');
                const p = JSON.parse(options.body); posts.push(p);
                return post ? post(p) : response(201, receipt(p));
            }
            assert.equal(url, '/api/locations');
            return response(200, [{id: 'ROOM', display_path: '302室'}, {id: 'CORRIDOR', display_path: '公共走廊'}]);
        },
    });
    await tick();
    return {node, fields, form, posts, storage, resets: () => resets};
}
test('opening the plan form preserves draft and does not submit', async () => {
    const p = await intake();
    p.fields.requirements.value = '保留草稿';
    assert.equal(p.node('#plan-intake-panel').open, false);
    await p.node('#plan-open-form').emit('click');
    assert.equal(p.node('#plan-intake-panel').open, true);
    assert.equal(p.fields.requirements.value, '保留草稿');
    assert.equal(p.resets(), 0); assert.equal(p.posts.length, 0);
});
test('pending submission stays visible and retries the identical IDs, places and times', async () => {
    const original = payload();
    const storage = new Map([[key, JSON.stringify({phase: 'pending', payload: original})]]);
    const p = await intake({storage});
    assert.equal(p.node('#plan-intake-panel').open, true);
    assert.equal(p.node('#plan-fields').disabled, true);
    assert.deepEqual(p.fields.location_ids.selectedOptions.map(o => o.value), ['ROOM', 'CORRIDOR']);
    p.fields.title.value = '不得覆盖待确认原提交';
    await p.form.emit('submit');
    assert.deepEqual(p.posts, [original]);
    assert.equal(p.node('#receipt').hidden, false);
    const restored = await intake({storage});
    assert.equal(restored.node('#plan-intake-panel').open, true);
    assert.equal(restored.node('#plan-id').textContent, planId);
    assert.equal(restored.posts.length, 0);
});
test('new submission retains multiple places and Beijing offsets', async () => {
    const p = await intake();
    const input = payload();
    for (const name of ['title', 'service_category', 'plan_kind', 'requirements', 'owner_name']) p.fields[name].value = input[name];
    p.fields.planned_start_at.value = '2026-10-13T09:30';
    p.fields.planned_end_at.value = '2026-10-13T10:30';
    for (const option of p.fields.location_ids.options) option.selected = true;
    await p.form.emit('submit');
    assert.deepEqual(p.posts, [{...input, request_id: 'request-new'}]);
    assert.equal(p.node('#receipt').hidden, false);
});
test('storage failure remains visible and blocks a new submission', async () => {
    const p = await intake({failRead: true});
    assert.equal(p.node('#plan-intake-panel').open, true);
    assert.equal(p.node('#send-button').disabled, true);
    assert.match(p.node('#message').textContent, /无法读取/);
    await p.form.emit('submit'); assert.equal(p.posts.length, 0);
});
async function listing({failDetail = false} = {}) {
    const nodes = new Map(), calls = [];
    const node = selector => { if (!nodes.has(selector)) nodes.set(selector, new Element()); return nodes.get(selector); };
    const record = receipt(payload());
    vm.runInNewContext(listSource, {AbortController, setTimeout, clearTimeout,
        document: {querySelector: node, createElement: () => new Element()},
        async fetch(url, options = {}) {
            calls.push({url, options});
            if (url === '/api/locations') return response(200, [{id: 'ROOM', display_path: '302室'}, {id: 'CORRIDOR', display_path: '公共走廊'}]);
            if (url === '/api/plans/' + planId) return failDetail ? response(503) : response(200, record);
            assert.equal(url, '/api/plans?limit=11&offset=0'); return response(200, [record]);
        },
    });
    await tick();
    return {node, calls};
}
test('four distinct plan actions keep the selected plan ID and read only', async () => {
    const p = await listing();
    const actions = p.node('#plan-rows').children[0].children[4].children[0];
    assert.equal(actions.className, 'plan-actions');
    const [detail, execution, inspection, check] = actions.children;
    assert.deepEqual(actions.children.map(x => x.textContent), ['计划详情', '执行记录', '现场检查', '记录核对']);
    assert.equal(execution.href, '/executions?plan_id=' + planId);
    assert.equal(inspection.href, '/inspections?plan_id=' + planId);
    assert.equal(check.href, '/plan-evidence?plan_id=' + planId);
    assert.equal(detail.type, 'button');
    await detail.emit('click');
    assert.equal(p.node('#plan-detail').hidden, false);
    assert.match(p.node('#plan-detail-fields').textContent, /302室.*公共走廊/);
    assert.equal(p.node('#plan-detail-requirements').textContent, payload().requirements);
    assert.ok(p.calls.every(call => !call.options.method));
});
test('failed plan detail lookup is not displayed as successful or empty detail', async () => {
    const p = await listing({failDetail: true});
    await p.node('#plan-rows').children[0].children[4].children[0].children[0].emit('click');
    assert.equal(p.node('#plan-detail').hidden, true);
    assert.match(p.node('#plan-detail-message').textContent, /未成功/);
});
