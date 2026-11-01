# 地点层级数据层与验收

日期：2026-11-01。使用合成数据；父地点业务规则与页面接线仍待实施。

## 文件与职责

| 文件 | 职责 |
| --- | --- |
| migrations/versions/0001_intake.py | 创建地点和报事表的固定初始版本 |
| migrations/versions/0002_plans.py | 增加计划及计划覆盖地点表 |
| migrations/versions/0003_location_hierarchy.py | 给地点增加parent_id、kind和相应约束 |
| models.py | 描述当前表结构及ORM关系 |
| location_schemas.py | 校验地点输入字段及单条记录的父地点规则 |
| verify_location_upgrade.py | 只读核对备份、实际版本、四表原字段和新列默认值 |

## 两种关系

PlanLocation.location_id表示计划明确选中的地点；Location.parent_id表示地点归属。
给大堂补上父楼栋，不会新增或修改plan_locations中的覆盖记录。
原计划只覆盖大堂时，补充归属后仍只覆盖大堂。
扩大计划范围需要明确的业务修改，不能由地点归属自动推导。
后续按归属查到相关记录，也不等于证明整栋已完成工作或供水正常。

## 已验证

- 地点保留原id/name/version，新增kind与可空parent_id。
- 父地点外键、禁止自父、类型及父地点是否必填的数据库约束。
- 旧地点初始化写法兼容；默认unknown类型和空父地点。
- 最新迁移与完整ORM结构一致。
- 两处同名302室保留不同ID及父楼栋，重连后仍能区分。
- 既有报事、计划与地点之间的ORM关系仍可读取。
- 迁移中途失败整体回滚；重复升级不改变数据。
- 已有分类或父关系时拒绝自动降级，防止丢失信息。

Python全量99项已在Windows本机及Linux验证环境通过。
组成：计划网页检查点82项 + 地点输入8项 + 地点迁移6项 + 地点模型3项。
实际库已升级到0003_location_hierarchy，升级前后只读核对结果如下。

| 业务表 | 记录数 | 原字段保留 |
| --- | --- | --- |
| locations | 3 | 是，比较原id/name/version |
| service_requests | 6 | 是，比较全部列 |
| plan_instances | 3 | 是，比较全部列 |
| plan_locations | 6 | 是，比较全部列 |

旧地点新字段均为kind=unknown、parent_id为空；完整性和外键检查通过。
备份路径与操作说明见[数据库迁移说明](database-migrations.md)。

## 当前边界

父地点类型是否合法、多节点环、完整地址输出和范围匹配尚未实现。
GET /api/locations与页面仍使用原地点输出；本次未增加地点管理后台。
不完整的台账不能代表全楼全部房间，也不能证明计划已经完成。
没有真实模型效果、生产部署或真实物业现场验证结论。
本轮CI需在推送对应提交后核对，不用上一检查点CI替代。