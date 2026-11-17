# 巡查记录数据层与升级验收

日期：2026-11-04。使用合成数据完成 Windows 本机验证。

## 业务与数据范围

巡查记录与执行声称分别保存，关联同一项计划。
一条巡查记录对应一个明确点位；不同点位有不同观察时分别记录。
具体点位或观察时间未知时保留为空，不使用计划范围或录入时间代填。

finding 取值为 no_issue_observed、issue_observed、unable_to_verify，
分别表示未见异常、发现异常、无法核实。
这些值描述填报的巡查观察，不直接判定计划完成或执行人员虚报。

## 三层定义的对应关系

| 定义 | 职责 |
| --- | --- |
| inspection_schemas.py / InspectionCreate | 校验提交的七个输入字段，拒绝客户端提供服务端字段 |
| models.py / InspectionRecord | 将 Python 对象映射到 inspection_records，并处理 UTC 时间和关联读取 |
| 0005_inspection_records.py | 在已有数据库中创建同一张表及固定约束 |

输入字段为 request_id、plan_id、inspector_name、description、
finding、location_id、observed_at。
数据库另保存服务端生成的 id、recorded_at 和 version，共十列。
request_id 用于识别同次提交；id 用于定位保存后的巡查记录。

表通过外键关联计划和可为空的地点，不要求存在某条执行记录。
数据库限制重复提交编号、非法枚举、文本长度、版本和观察/录入时间关系。
ORM 复用 UTCDateTime：已知时间归一 UTC，未知观察时间保留为空。
地点是否属于所选计划属于跨表业务规则，在后续 API 中实现。

## 自动回归

| 范围 | 数量 | 覆盖内容 |
| --- | --- | --- |
| 巡查输入 | 9 | 原文、单点位、未知信息、时间时区、枚举及服务端字段保护 |
| 巡查迁移 | 8 | 新库、旧六表保留、重复升级、约束、失败回滚及降级保护 |
| 巡查 ORM | 6 | 迁移与七表模型一致、关系/UTC读回、未知值、重复/非法写入及事务回滚 |
| 只读升级核对工具 | 6 | 全字段/结构对照、版本、外键、文件只读、缺失路径及新表初始状态 |

原有 162 项加上述 29 项，Python 全量共 191 项，本机 Windows 已通过。
JavaScript 表单回归另计 15 项，此数据层没有修改前端脚本。
GitHub Actions 在 Windows、Ubuntu 运行两组回归，结果以对应提交为准。
数据库相关测试使用临时数据库，并阻止连接默认项目数据库。

## 本机实际升级

版本从 0004_execution_records 升级至 0005_inspection_records。
维护期间停止服务，使用 SQLite backup 生成本次新备份：

data/backups/property-before-alembic-9cbf0689f6094245bddeebdde2522e2a.db

升级前使用 verify_inspection_upgrade.py 与 --before 核对，
成功后执行 python -m alembic upgrade 0005_inspection_records，
升级后使用同一备份、不加 --before 再次核对。

本机验收结果：

- 当前结构版本为 0005_inspection_records。
- 原 locations、service_requests、plan_instances、plan_locations、
  execution_records、execution_locations 六表的结构与全部行值保留。
- 数据库完整性与外键检查通过。
- 新 inspection_records 表在首次升级验收时为 0 条。
- 备份与实际数据库保留本机，不提交 Git。

核对脚本使用只读连接，发现差异时报告错误，不自动恢复或清理记录。
比较包含全部字段，不仅比较数量；测试用文件哈希验证核对过程没有改写数据库。

## 当前边界

此检查点完成输入、数据库结构、ORM、实际升级及核对工具。
巡查录入/查询 API、网页和计划证据核对随后接入。
新表为空只适用于本次结构升级验收；之后录入巡查属于正常业务变化。
已有巡查证据时，0005 自动降级会拒绝删除该表。
当前测试未调用真实模型，不代表 AI 研判效果评估。