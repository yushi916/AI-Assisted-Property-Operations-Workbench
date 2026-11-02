# 数据库迁移：初始版本与已有库接入

日期：2026-10-30。使用合成数据完成本机验证。

## 两种起点

- 全新数据库：执行迁移创建业务表，并记录迁移版本。
- 已有第一阶段数据库：核对结构、备份并验证业务记录，再登记固定版本0001_intake。
- 后续结构升级：编写新的迁移版本，使用upgrade升级。

upgrade执行迁移脚本；stamp只登记版本。
不能仅凭表名相同就登记版本，也不能用可变的head代替明确的初始版本。

## 新环境

在项目根目录、已安装依赖的Python 3.12环境中执行：

```bat
python -m alembic upgrade head
python seed_demo_location_hierarchy.py
```

地点初始化保留既有地点和报事，不自动给未知地点补关联。

## 已有第一阶段数据库

暂时停止服务写入，先只读核对：

```bat
python adopt_intake_database.py
```

status为ready后执行：

```bat
python adopt_intake_database.py --apply
python -m alembic current
```

成功返回adopted、记录条数和备份路径，当前版本为0001_intake。
已经登记且结构相符时返回already_managed，不重复备份。
缺少文件、结构不符或存在其他迁移版本时拒绝接入。

备份位于data/backups/，使用SQLite backup接口生成，再重新打开验证。
备份与数据库保留本机，不提交到Git。
初始接入工具不用于后续版本升级。

## 初始版本保护与验证

结构基准由固定初始迁移在临时库生成。
核对列、类型、空值规则、主键、唯一约束、CHECK、外键及索引；
额外视图或触发器转人工核对，并检查数据库完整性与外键。

登记前在事务内重查当前记录与备份是否一致，登记后确认业务值未改变。
备份后出现新修改则停止；登记失败则回滚版本写入。
保留其他连接已经提交的新数据，不用旧备份覆盖它。

迁移测试和接入测试禁止连接默认项目数据库。
SQL和事务仍在真实临时SQLite数据库执行。

- 4项迁移测试：空库建表与读回、约束、已有库保护、中途失败回滚。
- 5项接入测试：只读检查、原值与备份、结构不符拒绝、登记失败回滚、备份后变化。
- 全量50项：原有41项加上述9项，Windows本机及Linux验证环境通过。
- 已有库本机验收：完成结构核对、备份、版本登记，并读回此前合成报事原文。

```bat
python -m unittest discover -s tests -v
```

GitHub Actions结果以对应提交的实际运行记录为准。
上述50项对应初始迁移检查点。计划迁移见下节；当前验证使用合成数据。

## 0002_plans：计划数据层

版本链为0001_intake → 0002_plans；初始迁移保持原样。
新增plan_instances与plan_locations，计划可以覆盖多个明确地点。
同一计划的地点不能重复，关联的计划和地点必须存在。
routine/special仅表示计划种类，专项准备检查尚未实现。

已有0001数据库升级时，先停止服务写入，使用SQLite backup生成备份，
保留备份完整路径，再运行python -m alembic upgrade 0002_plans。
使用python -m alembic current确认实际库为0002_plans，
并与备份逐字段比较locations和service_requests的全部记录。
核对失败应保留现场查明原因，不自动覆盖当前库。

python -m alembic heads显示脚本最新版本，current显示实际数据库版本。
初始接入工具仍固定处理0001，不用于0002升级或重新登记版本。

新增7项计划输入、6项计划数据库、5项计划迁移测试，全量共68项，
Windows本机及Linux验证环境通过。
计划迁移测试覆盖空库完整结构、旧数据各字段保留、重复升级、
实际迁移约束，以及中途失败时表和版本回滚。
迁移测试使用临时数据库，并阻止连接默认项目数据库。

计划接口与页面已完成本机验收；执行/巡查及报事关联仍待实现。

## 2026-10-31 实际数据库数据保留核对

当次核对时数据库版本为0002_plans。
用于对照的备份为data/backups/property-before-alembic-11ff9fc11d3c486290794782ab58e49b.db，
已只读确认其版本为0001_intake。

| 旧业务表 | 备份记录数 | 当前记录数 | 缺失或改变的原记录 | 额外记录 |
| --- | --- | --- | --- | --- |
| locations | 3 | 3 | 0 | 0 |
| service_requests | 6 | 6 | 0 | 0 |

使用只读连接读取两张旧表的全部列，按完整行集合对照，而非仅比较数量。
本次原有3条地点、6条报事的全部字段保持一致。
该核对不统计新增的计划表，也不替代迁移失败回滚等临时库测试。
数据库和备份继续保留本机，不提交Git。


## 0003_location_hierarchy：兼容增加地点层级字段

版本链为0001_intake → 0002_plans → 0003_location_hierarchy。
在locations原表增加parent_id与kind；原id/name/version及其他业务表保持。
parent_id为空或引用已有地点，禁止地点引用自己；kind限制为明确枚举，
小区根及未分类地点不填父地点，其余类型必须填写父地点。
旧地点默认kind=unknown、parent_id为空，不根据名称猜测归属。
该迁移负责字段与数据库约束；父地点类型、多节点环和完整地址现由业务层校验，
见[地点层级与完整地址验收](location-hierarchy-data-validation.md)。

已有0002数据库升级前，停止服务写入并使用SQLite backup生成新备份。
使用verify_location_upgrade.py与--expected-version 0002_plans核对备份和当前库；
成功后执行python -m alembic upgrade 0003_location_hierarchy，
再以同一脚本默认的0003预期版本核对原字段、完整性、外键和新列默认值。
脚本只读；发现差异时保留现场核实，不自动恢复或覆盖当前库。

2026-11-01实际库验收：0003_location_hierarchy(head)。
对照备份data/backups/property-before-alembic-2a6608f8c70f414cb8f6cdf7c673e515.db，
原3条地点、6条报事、3条计划、6条覆盖关联的全部原字段保留；新列默认值正确。

新增8项地点输入、6项地点迁移及3项地点模型测试，该结构迁移检查点全量99项。
迁移测试验证中途失败回滚、原字段保留、重复升级、约束及降级保护；
模型测试对照最新迁移与完整模型结构，并检查同名房间和已有引用。
历史0001/0002测试使用固定版本和对应旧字段准备数据。
已登记分类或父关系时，0003的自动降级会拒绝，防止丢失层级信息。
数据库和备份继续保留本机；CI结果按本轮提交单独核对。

## 2026-11-01 合成台账维护：结构版本仍为0003

本次是台账数据维护，没有新增迁移或重建数据库。
使用同为0003的新备份data/backups/property-before-alembic-a801f27975774c96b49140db30e55fb9.db。
应用前用verify_demo_location_hierarchy.py --before严格核对；
运行seed_demo_location_hierarchy.py，首次新增9/更新3，重复执行新增0/更新0；
应用后以同一备份核对，地点从3条增至12条，原6条报事、3条计划、6条覆盖关联全部字段保持。
原地点编号与名称保留，分类/归属按合成配置调整，版本各加1。

verify_location_upgrade.py用于0002到0003结构迁移当时的默认值验收；
verify_demo_location_hierarchy.py用于本次台账数据调整的前后核对。
网页验收后来新增的合成业务记录属于正常变化，不能继续要求当前库与旧备份完全相等。
新的数据维护需使用该次操作的新备份与明确变更规则。
