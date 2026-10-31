const rows = document.querySelector("#plan-rows");
const listMessage = document.querySelector("#plan-list-message");
const detailMessage = document.querySelector("#plan-detail-message");
const previousButton = document.querySelector("#plan-previous");
const nextButton = document.querySelector("#plan-next");
const refreshButton = document.querySelector("#refresh-plans");
const detail = document.querySelector("#plan-detail");
const pageSize = 10;
const categories = {cleaning: "环境保洁", water_supply: "供水", other: "其他事项"};
const kinds = {routine: "日常计划", special: "专项计划"};
let offset = 0;
let hasNext = false;
let listBusy = false;
let detailSequence = 0;

function timeText(value) {
    return new Date(value).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai", hour12: false,
    });
}

async function getJSON(path) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
        const response = await fetch(path, {signal: controller.signal});
        if (response.status === 404) throw new Error("未找到这项计划。");
        if (!response.ok) throw new Error("查询暂未成功，请重试。");
        return await response.json();
    } finally {
        clearTimeout(timeout);
    }
}

async function locationText(ids) {
    try {
        const locations = await getJSON("/api/locations");
        const names = new Map(locations.map(item => [item.id, item.name]));
        return ids.map(id => names.has(id)
            ? `${names.get(id)}（${id}）` : `${id}（当前台账未查到）`).join("、");
    } catch {
        return ids.map(id => `${id}（名称暂无法读取）`).join("、");
    }
}

function updateButtons() {
    previousButton.disabled = listBusy || offset === 0;
    nextButton.disabled = listBusy || !hasNext;
    refreshButton.disabled = listBusy;
}

async function openPlan(id) {
    const sequence = ++detailSequence;
    detail.hidden = true;
    detailMessage.textContent = "正在查询计划详情…";
    try {
        const plan = await getJSON("/api/plans/" + encodeURIComponent(id));
        if (sequence !== detailSequence) return;
        if (plan.id !== id) throw new Error("详情与所选计划不一致，请重新查询。");
        const places = await locationText(plan.location_ids);
        if (sequence !== detailSequence) return;
        const values = [
            ["计划编号", plan.id],
            ["提交编号", plan.request_id],
            ["计划标题", plan.title],
            ["服务类别", categories[plan.service_category] ?? plan.service_category],
            ["计划种类", kinds[plan.plan_kind] ?? plan.plan_kind],
            ["负责人", plan.owner_name],
            ["覆盖地点（当前台账）", places],
            ["开始时间（北京时间）", timeText(plan.planned_start_at)],
            ["结束时间（北京时间）", timeText(plan.planned_end_at)],
            ["创建时间（北京时间）", timeText(plan.created_at)],
            ["记录版本", plan.version],
        ];
        const container = document.querySelector("#plan-detail-fields");
        container.replaceChildren();
        for (const [label, value] of values) {
            const term = document.createElement("dt");
            const description = document.createElement("dd");
            term.textContent = label;
            description.textContent = value;
            container.append(term, description);
        }
        document.querySelector("#plan-detail-requirements").textContent = plan.requirements;
        detail.hidden = false;
        detailMessage.textContent = "已读取保存的计划。执行和巡查证据尚待分别录入。";
        document.querySelector("#plan-detail-title").focus();
    } catch (error) {
        if (sequence !== detailSequence) return;
        detailMessage.textContent = error.name === "AbortError"
            ? "详情查询超时，请再次点击查看。" : error.message;
    }
}

async function loadPlans(targetOffset) {
    if (listBusy) return;
    listBusy = true;
    updateButtons();
    listMessage.textContent = "正在查询计划…";
    try {
        const plans = await getJSON(`/api/plans?limit=${pageSize + 1}&offset=${targetOffset}`);
        if (!Array.isArray(plans)) throw new Error("列表返回格式异常，请重试。");
        offset = targetOffset;
        hasNext = plans.length > pageSize;
        rows.replaceChildren();
        detailSequence++;
        detail.hidden = true;
        detailMessage.textContent = "";
        for (const plan of plans.slice(0, pageSize)) {
            const row = document.createElement("tr");
            for (const value of [timeText(plan.created_at), plan.title, plan.owner_name, plan.location_ids.length]) {
                const cell = document.createElement("td");
                cell.textContent = value;
                cell.style.overflowWrap = "anywhere";
                row.append(cell);
            }
            const action = document.createElement("td");
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = "查看详情";
            button.addEventListener("click", () => openPlan(plan.id));
            action.append(button);
            row.append(action);
            rows.append(row);
        }
        const count = Math.min(plans.length, pageSize);
        document.querySelector("#plan-page-label").textContent =
            `第 ${offset / pageSize + 1} 页，本页 ${count} 条`;
        listMessage.textContent = count ? "列表已更新。" : "当前没有可显示的计划。";
    } catch (error) {
        listMessage.textContent = error.name === "AbortError"
            ? "列表查询超时，请点击刷新重试。" : error.message;
    } finally {
        listBusy = false;
        updateButtons();
    }
}

previousButton.addEventListener("click", () => loadPlans(Math.max(0, offset - pageSize)));
nextButton.addEventListener("click", () => loadPlans(offset + pageSize));
refreshButton.addEventListener("click", () => loadPlans(0));
loadPlans(0);