# Property Operations Triage Agent v2

面向住宅物业人员的事项与履约证据工作台。
使用合成数据，分阶段开发，保留人工复核。

## 目标能力

1. 报事受理与证据研判。
2. 计划、执行和巡查记录核对，并与居民报事关联。
3. 专项任务准备检查。

人员明确关联某次计划后，系统将联合查看相关执行和巡查记录。
关联不等于同一原因，也不直接证明任务未完成或记录虚假。

系统区分观察时间与接收时间。
例如保洁完成后再次发现垃圾，需要考虑再次污染、地点差异及证据缺失。

程序只读查询业务记录并组装证据快照，再由 Gemini 生成结构化核查建议。
地点、时间比较和记录覆盖由程序处理；模型补充原文语义范围分析与下一步核查建议。
结果供工作人员复核；当前产品尚未实现正式审核、人工修正保存或结案流程。

## 当前实现

已完成本机报事、计划、执行和巡查记录的录入与查询，并支持计划证据核对：

- 报事：网页录入、保存回执、列表分页和详情查询。
- 计划：录入要求、负责人、时间区间与明确覆盖地点，保存后可查询。
- 地点：分区、楼栋、单元、房间及公共点位的层级与完整地址。
- 执行：按计划保存执行声称、明确点位和已知时间，支持录入、列表与详情。
- 现场检查（部分代码使用 inspection / 巡查命名）：按计划保存独立观察，未知地点和观察时间保留为空。
- 提交：先暂存编号和内容；结果未确认时刷新恢复，使用原编号和原内容重试。
- 时间：已知时间按 UTC 保存，网页按北京时间展示；未知时间不使用录入时间代替。
- 证据核对：读取所选计划的全部执行和巡查，按明确点位提示缺项、未知信息与时间关系。
- 核对网页：从计划列表进入，展示覆盖情况、待核实项、时间比较和全部证据引用，支持查询失败后重试。
- 人工补充计划：支持建立、查询及撤销关联，保留关联历史，不改写原业务记录。自动核查不要求先建立人工关联。
- AI 辅助核查：从报事查找候选报事和计划，读取计划所属执行及现场检查记录，保存证据快照和结构化建议。
- 依据查看：可从引用定位原文；已保存 AI 结果使用生成时快照，补录材料后需另发起核查，旧结果不会自动变化。
- 运行记录：保留 running / pending_review / failed 状态及错误阶段、类别；同一提交编号重查不会重复调用模型。

执行记录只保存明确声明的点位；未知点位保留为空列表。
计划覆盖 A、B 两处，不自动表示一条执行记录覆盖两处。
完成声称、巡查观察和风险声明仍需人员复核；程序提示不直接认定履约合格或虚报。

浏览器暂存使用当前标签页的 sessionStorage。
结果未确认时应保留该标签页；关闭标签页或浏览器后的草稿恢复尚未实现。

当前源码的 Alembic head 为 `0008_floor_locations`，使用 Alembic 管理增量迁移。
已有库升级按备份、结构及全字段前后核对流程操作，见[数据库迁移说明](docs/database-migrations.md)。
计划证据核对接口只读；人工关联接口写入独立关联表，保存关联不会改写原报事、计划、执行或巡查记录。

截至 2026-11-16，当前隔离副本 Python 全套 456 个测试方法通过。JavaScript 与网页人工验收沿用各自历史记录，不与本轮 Python 结果合并为业务案例数量。
验证层次、有限真实模型结果和未解决问题见[验证结果与限制](docs/validation-status-20261116.md)。
规则核对和人工关联的历史记录分别见[计划证据核对验收](docs/plan-evidence-validation.md)、[报事与计划人工关联](docs/report-plan-links-validation.md)。这些分阶段记录保留其当时范围，最新状态以上述汇总为准。


## 报事接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/service-requests` | 创建报事或重试同一次提交 |
| GET | `/api/service-requests` | 分页查询报事列表 |
| GET | `/api/service-requests/{report_id}` | 按报事 id 查询详情 |
| GET | `/api/locations` | 查询地点台账 |

提交行为：

- 首次合法提交返回 201。
- 相同 request_id、相同内容重试，返回 200 和原记录。
- 同一 request_id 用于不同内容，返回 409，原记录保持不变。
- 输入不合法、地点不存在或新报事的观察时间晚于接收时间，返回 422。
- 写入时数据库繁忙，返回 503，可以保留原提交编号重试。

request_id 是提交编号，用于重复提交保护。
id 是服务端生成的报事编号，查询详情时填写这个 id。

## 计划接口与页面

计划页面：http://127.0.0.1:8000/plans

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/plans` | 创建计划或重试同一次提交 |
| GET | `/api/plans` | 分页查询计划，返回完整的覆盖地点 ID |
| GET | `/api/plans/{plan_id}` | 按内部计划 id 查询详情 |

计划至少覆盖一个已登记地点，计划结束时间必须晚于开始时间。
首次保存返回201，同号同内容重试返回200，同号换内容返回409。
网页使用北京时间；内部计划 id 与提交 request_id 用途不同。
保存计划不等于执行完成或自动派工；大堂点位不代表整个分区或楼栋。

## 主要文件

| 文件 | 负责什么 |
| --- | --- |
| `main.py` | 创建应用、注册接口、提供录入与列表网页 |
| `schemas.py` | 定义接口输入和输出的数据规则 |
| `plan_schemas.py` | 校验计划要求、覆盖地点和带时区的计划区间 |
| `models.py` | 定义数据库表、字段和约束 |
| `database.py` | 创建数据库引擎并提供会话 |
| `routers/reports.py` | 处理报事 HTTP 请求、读写数据库并返回结果 |
| `routers/locations.py` | 只读查询地点台账 |
| `seed_demo_locations.py` | 显式加入合成地点，不覆盖既有地点或报事 |
| `automatic_evidence.py` / `automatic_agent_evidence.py` | 只读取证、查询状态和证据快照投影 |
| `gemini_agent.py` | Gemini SDK、动态 JSON Schema 和单次生成 |
| `agent_evidence.py` / `source_state_rules.py` | 应用层结构、引用、候选覆盖与有限来源状态检查 |
| `agent_runner.py` / `routers/agent_runs.py` | 运行阶段、错误处理、保存和旧结果读回 |
| `templates/` | 报事、计划、执行、现场检查和核查页面 |
| `static/` | 表单提交、恢复重试、列表与详情查询脚本 |
| `migrations/` | 保存固定的数据库迁移版本与连接入口 |
| `adopt_intake_database.py` | 核对并备份已有第一阶段数据库，登记初始版本 |
| `tests/` | 输入、数据库、接口与迁移回归测试 |

## 本地运行

使用 Python 3.12，先激活已有的 property-v2 环境：

```bat
conda activate property-v2
python -m pip install -r requirements-dev.txt
python -m pip check
```

全新数据库（尚无业务表），在项目根目录执行：

```bat
python -m alembic upgrade head
python seed_demo_location_hierarchy.py
python seed_demo_floor_locations.py
```

已有第一阶段数据库先按[数据库迁移说明](docs/database-migrations.md)完成结构核对、备份和初始版本登记。
已经接入迁移管理的数据库，后续结构升级使用新的迁移与 `python -m alembic upgrade head`。
层级脚本使用明确的合成配置，遇到名称或已有归属冲突会拒绝覆盖。
已有台账的分类调整按备份与前后核对流程操作，见[地点验收说明](docs/location-hierarchy-data-validation.md)。


非 AI 的记录与规则核对不需要模型密钥。使用 AI 功能时，在启动服务的同一 Windows CMD 中设置 `GEMINI_API_KEY`，并显式指定本轮使用的模型；密钥只保存在本机，不提交到仓库。代码读取进程环境变量，不自动加载 `.env` 文件。

```bat
set GEMINI_MODEL=gemini-3.5-flash-lite
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

模型调用超时配置为 20 秒、SDK 尝试次数为 1、输出上限 4096 tokens；没有自动函数调用。当前架构是程序取证后的一次建议生成，不是多 Agent 或开放式工具调用循环。

开发时可在启动命令末尾添加 `--reload` 自动重载代码。

- 录入页：http://127.0.0.1:8000/
- 报事列表：http://127.0.0.1:8000/reports
- 健康接口：http://127.0.0.1:8000/health
- 接口文档：http://127.0.0.1:8000/docs

## 验证

在项目根目录、已激活的 property-v2 环境运行 Python 回归。
JavaScript 交互回归使用 Node.js 24：

```bat
python -m unittest discover -s tests -v
node --test tests/test_execution_form.cjs tests/test_execution_navigation.cjs tests/test_inspection_form.cjs tests/test_plan_evidence_view.cjs tests/test_report_evidence_view.cjs tests/test_report_location_picker.cjs tests/test_agent_run_view.cjs tests/test_plan_navigation.cjs
```

当前 Python 全套结果为 456 个测试方法通过，包含本轮来源状态检查，不将子检查、重复运行或真实模型输出重复相加。此结果来自隔离副本；本轮补丁另获使用者“确认无误”，未将其扩大为 Windows 全套 456 项均已重跑。
JavaScript 历史检查及其版本范围见验证汇总；本次文档整理不重跑测试，不新增模型请求。

数据库相关测试使用临时数据库；JavaScript 测试模拟页面、存储与网络。
GitHub Actions 已配置 Windows、Ubuntu 的 Python 与 JavaScript 回归；当前远程提交及 CI 结果仍需发布时核对。本次收尾已在隔离副本为工作流补列已有 `test_plan_navigation.cjs`，修正后的 JavaScript 命令已离线验证；本机实际差异和远程 CI 仍待核对。配置文件存在不能写成远程 CI 已通过。
自动测试不等于真实浏览器端到端验证，也不证明 AI 研判质量。

实际浏览器验收与自动测试分别记录。
执行与巡查记录断线恢复后，均已用只读查询核实原提交编号对应保存回执，且各自记录数为 1。
证据核对页已完成正常显示、停服刷新失败和恢复服务后重新查询的本机验收。

- [第一阶段验收](docs/stage-1-validation.md)
- [数据库迁移](docs/database-migrations.md)
- [计划受理与查询验收](docs/plan-intake-validation.md)
- [地点层级验收](docs/location-hierarchy-data-validation.md)
- [执行记录接口验收](docs/execution-api-validation.md)
- [执行记录开发与网页验收](docs/execution-records-workflow.md)
- [巡查数据层验收](docs/inspection-data-validation.md)
- [巡查接口验收](docs/inspection-api-validation.md)
- [巡查记录开发与网页验收](docs/inspection-records-workflow.md)
- [计划证据核对验收](docs/plan-evidence-validation.md)


## 演示主线与边界

1. 打开报事列表，进入报事详情，核对居民原文、地点、实际观察时间与接收时间。
2. 发起一次 AI 辅助核查，查看事实摘要、候选判断、信息缺口和核查建议。
3. 点击依据对照本次快照原文，同时检查其他材料，区分引用有效、引用支持与事实引用覆盖。
4. 从计划查看执行填报及现场检查；需要时补充一条实际观察。规则核对只比较明确地点、记录数量和时间关系。
5. 读回旧 AI 结果，确认仍使用原证据快照。新增材料只有在新核查中才会纳入，不用重复生成挑选满意答案。

演示优先使用已验证的供水与保洁合成材料；H1/H2 是临时库取证及冻结输入评估，不冒充已经在网页重新录入并验收。信息栏探索案例用于展示未知时间、无候选和来源状态限制，不计完整新业务场景验收。

设备/管网、维修工单和通知尚未接入；查询为空、未查询、未接入和查询失败分别保留。候选不代表同一事件，填报完成或局部正常不代表问题已解决。模型仍可能漏引或产生范围歧义，最终需要人员复核。

正式审核、人工修正保存及审计、权限完善、公开部署、绿植和节日彩灯专项验证均未完成，不作为首版已交付能力。先完成真实仓库的提交范围、链接和对应 CI 核对，再根据实际证据编写简历；不为收尾新增架构或业务功能。