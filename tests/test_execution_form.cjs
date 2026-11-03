const vm = require("node:vm");
const {readFileSync} = require("node:fs");
const {join} = require("node:path");
const test = require("node:test");
const source = readFileSync(join(__dirname, "..", "static", "executions.js"), "utf8");
const assert = require("node:assert/strict");
const planId = "23832b15-693f-4ff8-a563-4a60fa7d42be";

class Element {
    constructor(value = "") {
        this.value = value;
        this.children = [];
        this.handlers = {};
        this.disabled = false;
        this.hidden = false;
        this.textContent = "";
    }
    get options() { return this.children; }
    get selectedOptions() { return this.children.filter(option => option.selected); }
    replaceChildren(...items) { this.children = items; }
    addEventListener(type, handler) { this.handlers[type] = handler; }
    async emit(type) { return this.handlers[type]?.({ preventDefault() {} }); }
    focus() {}
}

async function page({storage = new Map(), post, failContext = false, failWrite = false,
    failRead = false, failCatalog = false, query = "?plan_id=" + planId} = {}) {
    const fields = Object.fromEntries([
        "executor_name", "description", "completion_claim", "scope_mode",
        "location_ids", "claimed_started_at", "claimed_finished_at",
    ].map(name => [name, new Element()]));
    fields.completion_claim.value = "unknown";
    fields.scope_mode.value = "unknown";

    const nodes = new Map();
    const form = new Element();
    form.elements = { namedItem: name => fields[name] };
    form.reportValidity = () => true;
    form.reset = () => {
        Object.values(fields).forEach(field => { field.value = ""; });
        fields.completion_claim.value = "unknown";
        fields.scope_mode.value = "unknown";
        fields.location_ids.children.forEach(option => { option.selected = false; });
    };
    nodes.set("#execution-form", form);

    const calls = [], posts = [];
    let serial = 0;
    const context = vm.createContext({
        URLSearchParams, AbortController, setTimeout, clearTimeout,
        window: { location: { search: query } },
        crypto: {
            randomUUID: () => "00000000-0000-4000-8000-" + String(++serial).padStart(12, "0"),
        },
        sessionStorage: {
            getItem(key) {
                if (failRead) throw Error("read");
                return storage.get(key) ?? null;
            },
            setItem(key, value) {
                if (failWrite) throw Error("write");
                storage.set(key, value);
            },
            removeItem(key) { storage.delete(key); },
        },
        document: {
            querySelector(selector) {
                if (!nodes.has(selector)) nodes.set(selector, new Element());
                return nodes.get(selector);
            },
            createElement() { return new Element(); },
        },
        async fetch(url, options = {}) {
            calls.push(url);
            if (options.method === "POST") {
                const payload = JSON.parse(options.body);
                posts.push(payload);
                return post ? post(payload, posts.length) : response(201, receipt(payload));
            }
            if (failContext) throw new TypeError("network");
            if (url.startsWith("/api/plans/")) return response(200, {
                id: planId, location_ids: ["LOC-A-LOBBY", "LOC-B-LOBBY"],
            });
            if (failCatalog) throw new TypeError("catalog unavailable");
            return response(200, [
                {id: "LOC-A-LOBBY", display_path: "合成小区 / A区 / A区大堂"},
                {id: "LOC-B-LOBBY", display_path: "合成小区 / B区 / B区大堂"},
                {id: "LOC-A-COURTYARD", display_path: "不在计划内"},
            ]);
        },
    });
    await vm.runInContext(source, context);
    return {
        fields, nodes, form, storage, posts, calls,
        node: id => nodes.get("#execution-" + id),
        submit: () => form.emit("submit"),
        saved: () => JSON.parse(storage.get("property-v2-execution-intake-v1:" + planId)),
    };
}

function response(status, body) {
    return {status, ok: status >= 200 && status < 300, json: async () => body};
}
function receipt(payload) {
    return {
        ...payload,
        id: "d8346382-5e75-4f6c-acbb-54fd7710b2d7",
        recorded_at: "2026-11-03T07:16:00Z",
        version: 1,
    };
}
function fill(p, description = "合成验收：情况待核实") {
    p.fields.executor_name.value = "合成执行人";
    p.fields.description.value = description;
}

test("unknown stays [] and null; raw text is preserved", async () => {
    const p = await page();
    fill(p, "  <img src=x onerror=alert(1)> 原始合成描述  ");
    assert.equal(p.fields.scope_mode.value, "unknown");
    assert.equal(p.fields.location_ids.selectedOptions.length, 0);
    await p.submit();
    assert.deepEqual(p.posts[0].location_ids, []);
    assert.equal(p.posts[0].claimed_started_at, null);
    assert.equal(p.posts[0].claimed_finished_at, null);
    assert.equal(p.posts[0].plan_id, planId);
    assert.equal(p.saved().phase, "accepted");
    assert.ok(p.node("receipt-content").textContent.endsWith(p.posts[0].description));
    assert.match(p.node("receipt-content").textContent, /具体点位待核实/);
    assert.equal(p.node("receipt-content").children.length, 0);
});

test("only explicit A point is sent; no B or unrelated point", async () => {
    const p = await page();
    fill(p);
    assert.deepEqual(p.fields.location_ids.options.map(o => o.value),
        ["LOC-A-LOBBY", "LOC-B-LOBBY"]);
    p.fields.scope_mode.value = "known";
    p.fields.location_ids.options[0].selected = true;
    p.fields.completion_claim.value = "completed";
    p.fields.claimed_started_at.value = "2026-11-01T09:00";
    p.fields.claimed_finished_at.value = "2026-11-01T09:20";
    await p.submit();
    assert.deepEqual(p.posts[0].location_ids, ["LOC-A-LOBBY"]);
    assert.equal(p.posts[0].claimed_finished_at, "2026-11-01T09:20+08:00");
    assert.match(p.node("receipt-content").textContent, /合成小区 \/ A区 \/ A区大堂/);
});

test("known mode without a selected point sends nothing", async () => {
    const p = await page();
    fill(p);
    p.fields.scope_mode.value = "known";
    await p.submit();
    assert.equal(p.posts.length, 0);
    assert.equal(p.storage.size, 0);
    assert.match(p.node("submit-message").textContent, /至少一个/);
});

test("422 permits edits and preserves submission id", async () => {
    const p = await page({post: (payload, n) => n === 1
        ? response(422, {detail: [{msg: "不能仅包含空白字符"}]})
        : response(201, receipt(payload))});
    fill(p, "   ");
    await p.submit();
    assert.equal(p.saved().phase, "invalid");
    assert.equal(p.node("form-fields").disabled, false);
    fill(p, "修正后的合成描述");
    await p.submit();
    assert.equal(p.posts[0].request_id, p.posts[1].request_id);
    assert.equal(p.posts[1].description, "修正后的合成描述");
    assert.equal(p.saved().phase, "accepted");
});

test("ambiguous save then reload retries original payload, one record", async () => {
    const storage = new Map(), db = new Map();
    const backend = async payload => {
        const existing = db.has(payload.request_id);
        db.set(payload.request_id, payload);
        if (!existing) throw new TypeError("connection lost after server saved");
        return response(200, receipt(payload));
    };
    const p = await page({storage, post: backend});
    fill(p);
    await p.submit();
    assert.equal(p.saved().phase, "pending");
    assert.equal(db.size, 1);

    const q = await page({storage, post: backend, failContext: true});
    assert.equal(q.node("send").disabled, false);
    q.fields.description.value = "must not change frozen payload";
    await q.submit();
    assert.deepEqual(q.posts[0], p.posts[0]);
    assert.equal(db.size, 1);
    assert.equal(q.saved().phase, "accepted");
});

test("409 blocks editing and further submissions", async () => {
    const p = await page({post: () => response(409, {detail: "conflict"})});
    fill(p);
    await p.submit();
    await p.submit();
    assert.equal(p.saved().phase, "conflict");
    assert.equal(p.posts.length, 1);
    assert.equal(p.node("form-fields").disabled, true);
    assert.equal(p.node("new").hidden, true);
});

test("mismatched success receipt remains pending", async () => {
    const p = await page({post: payload =>
        response(201, {...receipt(payload), plan_id: "other"})});
    fill(p);
    await p.submit();
    assert.equal(p.saved().phase, "pending");
    assert.equal(p.node("receipt").hidden, true);
});

test("storage write failure makes no POST", async () => {
    const p = await page({failWrite: true});
    fill(p);
    await p.submit();
    assert.equal(p.posts.length, 0);
    assert.equal(p.storage.size, 0);
});

test("unreadable storage blocks sending", async () => {
    const p = await page({failRead: true});
    fill(p);
    await p.submit();
    assert.equal(p.posts.length, 0);
    assert.equal(p.node("send").disabled, true);
});

test("new record clears only this plan draft and starts a new id", async () => {
    const storage = new Map([["other-plan-draft", "keep"]]);
    const p = await page({storage});
    fill(p);
    await p.submit();
    const id = p.posts[0].request_id;
    await p.node("new").emit("click");
    assert.equal(storage.get("other-plan-draft"), "keep");
    fill(p);
    await p.submit();
    assert.notEqual(p.posts[1].request_id, id);
    assert.deepEqual(p.posts[1].location_ids, []);
});

test("missing selected plan never sends", async () => {
    const p = await page({query: ""});
    fill(p);
    await p.submit();
    assert.equal(p.calls.length, 0);
    assert.equal(p.posts.length, 0);
    assert.equal(p.node("send").disabled, true);
});

test("stored data from a different plan blocks submission", async () => {
    const storage = new Map();
    storage.set("property-v2-execution-intake-v1:" + planId, JSON.stringify({
        phase: "pending", payload: {
            request_id: "00000000-0000-4000-8000-000000000001",
            plan_id: "another-plan", executor_name: "合成执行人",
            description: "合成描述", completion_claim: "unknown",
            location_ids: [], claimed_started_at: null, claimed_finished_at: null,
        },
    }));
    const p = await page({storage});
    fill(p);
    await p.submit();
    assert.equal(p.posts.length, 0);
    assert.equal(p.node("send").disabled, true);
});

test("double click while saving sends only once", async () => {
    let finish;
    const p = await page({post: payload => new Promise(resolve => {
        finish = () => resolve(response(201, receipt(payload)));
    })});
    fill(p);
    const first = p.submit();
    try {
        await p.submit();
        assert.equal(p.posts.length, 1);
    } finally {
        finish();
        await first;
    }
    assert.equal(p.saved().phase, "accepted");
});

test("refresh restores accepted receipt without another POST", async () => {
    const storage = new Map();
    const p = await page({storage});
    fill(p);
    await p.submit();
    const q = await page({storage});
    await q.submit();
    assert.equal(q.posts.length, 0);
    assert.equal(q.saved().phase, "accepted");
    assert.equal(q.node("receipt").hidden, false);
    assert.match(q.node("receipt-content").textContent, /d8346382/);
});

test("catalog failure still permits explicit plan points by id", async () => {
    const p = await page({failCatalog: true});
    fill(p);
    assert.deepEqual(p.fields.location_ids.options.map(o => o.value),
        ["LOC-A-LOBBY", "LOC-B-LOBBY"]);
    assert.equal(p.node("send").disabled, false);
    p.fields.scope_mode.value = "known";
    p.fields.location_ids.options[1].selected = true;
    await p.submit();
    assert.deepEqual(p.posts[0].location_ids, ["LOC-B-LOBBY"]);
    assert.equal(p.saved().phase, "accepted");
});