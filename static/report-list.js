const rows = document.querySelector("#report-rows");
const listMessage = document.querySelector("#list-message");
const detailMessage = document.querySelector("#detail-message");
const previousButton = document.querySelector("#previous-page");
const nextButton = document.querySelector("#next-page");
const refreshButton = document.querySelector("#refresh-list");
const detail = document.querySelector("#report-detail");
const pageSize = 10;
let offset = 0;
let hasNext = false;
let listBusy = false;
let detailSequence = 0;

const categories = { cleaning: "环境保洁", water_supply: "供水", other: "其他事项" };
const risks = {
    unknown: "信息不足，风险待核实",
    no_immediate_risk_reported: "未报告即时风险",
    urgent: "已报告紧急风险",
};
const sources = { web: "网页", phone: "电话", on_site: "现场" };

function timeText(value) {
    if (!value) return "未知，待核实";
    return new Date(value).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai", hour12: false,
    });
}

async function getJSON(path) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
        const response = await fetch(path, { signal: controller.signal });
        if (response.status === 404) throw new Error("未找到这条报事。");
        if (!response.ok) throw new Error("查询暂未成功，请重试。");
        return await response.json();
    } finally {
        clearTimeout(timeout);
    }
}

async function getLocationText(id) {
    if (!id) return "未知，待核实";

    try {
        const locations = await getJSON("/api/locations");
        const location = locations.find(item => item.id === id);
        return location
            ? `${location.name}（${id}）`
            : `${id}（当前台账未查到）`;
    } catch {
        return `${id}（地点名称暂无法读取）`;
    }
}


function updateButtons() {
    previousButton.disabled = listBusy || offset === 0;
    nextButton.disabled = listBusy || !hasNext;
    refreshButton.disabled = listBusy;
}

async function openReport(id) {
    const sequence = ++detailSequence;
    detail.hidden = true;
    detailMessage.textContent = "正在查询详情…";
    try {
        const report = await getJSON("/api/service-requests/" + encodeURIComponent(id));
        if (sequence !== detailSequence) return;
        if (report.id !== id) throw new Error("详情与所选报事不一致，请重新查询。");
        const place = await getLocationText(report.location_id);
        if (sequence !== detailSequence) return;
        const values = [
            ["报事编号", report.id],
            ["提交编号", report.request_id],
            ["服务类别", categories[report.service_category] ?? report.service_category],
            ["来源", sources[report.source] ?? report.source],
            ["人员声明风险", risks[report.risk_status] ?? report.risk_status],
            ["关联地点（当前台账）", place],
            ["观察时间（北京时间）", timeText(report.observed_at)],
            ["接收时间（北京时间）", timeText(report.received_at)],
            ["记录版本", report.version],
        ];

        const container = document.querySelector("#detail-fields");
        container.replaceChildren();
        for (const [label, value] of values) {
            const term = document.createElement("dt");
            const description = document.createElement("dd");
            term.textContent = label;
            description.textContent = value;
            container.append(term, description);
        }

        document.querySelector("#detail-description").textContent = report.description;
        detail.hidden = false;
        detailMessage.textContent = "已读取保存记录。关联地点和风险声明不代表已经完成核实。";
        document.querySelector("#detail-title").focus();
    } catch (error) {
        if (sequence !== detailSequence) return;
        detailMessage.textContent = error.name === "AbortError"
            ? "详情查询超时，请再次点击查看。"
            : error.message;
    }
}

async function loadReports(targetOffset) {
    if (listBusy) return;
    listBusy = true;
    updateButtons();
    listMessage.textContent = "正在查询报事…";

    try {
        const reports = await getJSON(
            `/api/service-requests?limit=${pageSize + 1}&offset=${targetOffset}`
        );
        if (!Array.isArray(reports)) throw new Error("列表返回格式异常，请重试。");

        offset = targetOffset;
        hasNext = reports.length > pageSize;
        rows.replaceChildren();

        for (const report of reports.slice(0, pageSize)) {
            const row = document.createElement("tr");
            const summary = report.description.length > 80
                ? report.description.slice(0, 80) + "…"
                : report.description;
            const values = [
                timeText(report.received_at),
                categories[report.service_category] ?? report.service_category,
                summary,
                risks[report.risk_status] ?? report.risk_status,
            ];

            for (const value of values) {
                const cell = document.createElement("td");
                cell.textContent = value;
                cell.className = "description";
                row.append(cell);
            }

            const action = document.createElement("td");
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = "查看详情";
            button.addEventListener("click", () => openReport(report.id));
            action.append(button);
            row.append(action);
            rows.append(row);
        }

        const count = Math.min(reports.length, pageSize);
        document.querySelector("#page-label").textContent =
            `第 ${offset / pageSize + 1} 页，本页 ${count} 条`;
        listMessage.textContent = count ? "列表已更新。" : "当前没有可显示的报事。";
    } catch (error) {
        listMessage.textContent = error.name === "AbortError"
            ? "列表查询超时，请点击刷新重试。"
            : error.message;
    } finally {
        listBusy = false;
        updateButtons();
    }
}

previousButton.addEventListener("click", () => loadReports(Math.max(0, offset - pageSize)));
nextButton.addEventListener("click", () => loadReports(offset + pageSize));
refreshButton.addEventListener("click", () => loadReports(offset));
loadReports(0);