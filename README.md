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

后续由只读 Agent 查询证据并生成建议；
确定性规则由程序校验，最终决定由人员审核。

## 当前实现

已完成本机报事、计划、执行和巡查记录的录入与查询，并支持计划证据核对：

- 报事：网页录入、保存回执、列表分页和详情查询。
- 计划：录入要求、负责人、时间区间与明确覆盖地点，保存后可查询。
- 地点：分区、楼栋、单元、房间及公共点位的层级与完整地址。
- 执行：按计划保存执行声称、明确点位和已知时间，支持录入、列表与详情。
- 巡查：按计划保存独立观察，未知地点和观察时间保留为空。
- 提交：先暂存编号和内容；结果未确认时刷新恢复，使用原编号和原内容重试。
- 时间：已知时间按 UTC 保存，网页按北京时间展示；未知时间不使用录入时间代替。
- 证据核对：读取所选计划的全部执行和巡查，按明确点位提示缺项、未知信息与时间关系。
- 核对网页：从计划列表进入，展示覆盖情况、待核实项、时间比较和全部证据引用，支持查询失败后重试。

执行记录只保存明确声明的点位；未知点位保留为空列表。
计划覆盖 A、B 两处，不自动表示一条执行记录覆盖两处。
完成声称、巡查观察和风险声明仍需人员复核；程序提示不直接认定履约合格或虚报。

浏览器暂存使用当前标签页的 sessionStorage。
结果未确认时应保留该标签页；关闭标签页或浏览器后的草稿恢复尚未实现。

当前数据库结构版本为 0005_inspection_records，使用 Alembic 管理增量迁移。
已有库升级按备份、结构及全字段前后核对流程操作，见[数据库迁移说明](docs/database-migrations.md)。
本轮证据核对不改数据库结构、不写业务记录，也不调用模型。

Windows 本机已确认 Python 239 项通过；JavaScript 45 项沿用此前已通过的检查。
规则、接口、网页及业务案例的验证记录见[计划证据核对验收](docs/plan-evidence-validation.md)。
报事与计划关联、只读 Agent、人工审核及专项准备检查继续分阶段实现。


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
| `templates/` | 报事录入、列表和详情展示的网页结构 |
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
```

已有第一阶段数据库先按[数据库迁移说明](docs/database-migrations.md)完成结构核对、备份和初始版本登记。
已经接入迁移管理的数据库，后续结构升级使用新的迁移与 `python -m alembic upgrade head`。
层级脚本使用明确的合成配置，遇到名称或已有归属冲突会拒绝覆盖。
已有台账的分类调整按备份与前后核对流程操作，见[地点验收说明](docs/location-hierarchy-data-validation.md)。


```bat
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

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
node --test tests/test_execution_form.cjs tests/test_inspection_form.cjs tests/test_plan_evidence_view.cjs
```

当前 Python 共 239 项：巡查网页检查点 207 项，加规则 12 项、核对 API 10 项、核对页面 4 项和关联输入 6 项。
JavaScript 共 45 项：执行 15 项、巡查 18 项、核对页面 12 项。
Windows 本机与隔离副本均已通过；新里程碑的远程 CI 在推送后按对应提交核对。

数据库相关测试使用临时数据库；JavaScript 测试模拟页面、存储与网络。
GitHub Actions 配置在 Windows、Ubuntu 运行这两组回归。
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


## 后续阶段

1. 在第二阶段继续报事和计划的人工关联，以及联合证据视图。
2. 接入只读 Agent，保存建议、证据与失败记录。
3. 完成人工修正、批准或退回、审计和内部核实待办。
4. 完成专项准备检查、权限完善、部署与评估展示；表结构变化持续通过迁移管理。
