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


## 0004_execution_records：执行证据数据层

日期：2026-11-02。版本从0003_location_hierarchy升级至0004_execution_records。
新增execution_records与execution_locations，旧四张业务表结构保持。
执行主记录关联一次计划，点位表只保存本条执行记录明确涉及的地点。
未知点位保留为空，未知声称时间保留为空，不用计划范围或录入时间填补。
completed表示执行人声称完成，不能直接作为独立核实结果。

输入校验、ORM、迁移与只读升级核对已实现；执行API和网页尚未接线。
计划存在性、执行点位属于计划的范围校验，以及完整HTTP幂等语义将在API实现。
新增表不改变原计划版本及覆盖地点，也不自动派工或标记计划完成。
已有任何执行记录时，即使点位未知，0004自动降级也会拒绝删除证据。

### 实际数据库验收

本次新备份：data/backups/property-before-alembic-c311abd931054d94bd19fdf4a6f8c316.db。
备份版本为0003；升级前核对通过后，执行alembic upgrade 0004_execution_records。
实际current已确认0004_execution_records(head)。
verify_execution_upgrade.py升级前使用--before，升级后使用同一备份且不加此参数。
两次核对均通过旧表结构、全部字段和记录、数据库完整性与外键检查。

| 表 | 升级前备份 | 升级后当前库 | 结果 |
| --- | --- | --- | --- |
| locations | 12 | 12 | 全字段一致 |
| service_requests | 7 | 7 | 全字段一致 |
| plan_instances | 5 | 5 | 全字段一致 |
| plan_locations | 8 | 8 | 全字段一致 |
| execution_records | 尚无此表 | 0 | 新表为空 |
| execution_locations | 尚无此表 | 0 | 新表为空 |

此核对适用于本次纯结构升级；开始业务录入后，新记录属于正常变化。
核对工具使用只读连接，发现差异会报错，不删除记录或覆盖当前库。
数据库和备份保留本机，不提交Git。

### 开发步骤简记：执行记录数据层

| 顺序 | 代码或操作 | 主要验证 |
| --- | --- | --- |
| 1 | execution_schemas.py定义输入 | 声称枚举、未知信息、原文和带时区时间规则，9项 |
| 2 | 编写0004迁移，先在临时库升级 | 约束、旧值保留、失败回滚及降级保护，8项 |
| 3 | models.py补执行模型，更新当前表清单 | 最新六表与迁移一致，ORM读回、UTC和事务，6项 |
| 4 | 实际库确认0003，使用SQLite backup创建新备份 | 升级前只读核对结构及原记录 |
| 5 | 实际库执行alembic upgrade 0004_execution_records | 这一步改变实际数据库结构及版本 |
| 6 | verify_execution_upgrade.py进行升级后只读对照 | 原四表全字段保留，新两表为空；工具自身有6项测试 |

新增29项，加上此前117项，全量146项已在Linux验证环境通过。
上述实际升级及前后JSON由Windows本机完成并提供；CI结果按对应提交另行核对。
执行记录整轮仍需继续接口、页面和业务范围校验。


## 0005_inspection_records：巡查观察数据层

日期：2026-11-04。版本从0004_execution_records升级至0005_inspection_records。
新增inspection_records单表，关联计划和一个可为空的明确地点。
观察时间与录入时间分别保存；未知地点和观察时间保留为空。
原有六张业务表的结构和数据保留，已有执行记录可以非空。

本次Windows实际升级已完成停服、备份、升级前核对、升级和升级后核对。
本次备份为data/backups/property-before-alembic-9cbf0689f6094245bddeebdde2522e2a.db。
当前版本已确认0005_inspection_records；
旧六表全字段及结构一致，完整性和外键检查通过，新巡查表为0条。
具体规则与验证见[巡查数据层验收](inspection-data-validation.md)。

verify_inspection_upgrade.py升级前使用--before，升级后使用同一备份不加该参数。
工具只读，不覆盖当前库；新表为空的检查仅用于本次纯结构升级。
后续录入巡查后，不再把新表非空当成升级失败。

新增巡查输入9项、迁移8项、ORM6项和核对工具6项，
Python全量191项在Windows本机通过，JavaScript表单另计15项。
本检查点CI结果以对应提交的Actions为准。
巡查API和页面随后接入，计划范围校验在API实现。
已有巡查证据时自动降级拒绝删除；数据库和备份继续保留本机。


## 0006_request_plan_links：人工关联记录

日期：2026-11-06。新增request_plan_links，旧七张业务表保留。
实际升级后版本为0006；只读核对确认旧表结构、全字段记录、完整性与外键均通过。
当时旧表数量：地点12、报事7、计划5、计划地点8、执行6、执行地点2、巡查7；新关联表为0。

verify_plan_link_upgrade.py升级前使用同次备份加--before，升级后使用该备份且不加--before。
新关联表为空的检查仅适用于纯结构升级，业务录入后不再使用这一条件判断升级结果。
已有任何关联历史时拒绝自动降级删除；业务库和备份保留本机。

规则及验收见[报事与计划人工关联](report-plan-links-validation.md)。