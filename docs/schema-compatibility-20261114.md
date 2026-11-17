# 最小结构成功、完整结构400：局部兼容修正

2026-11-14，用户独立诊断反馈：同模型、同测试文字、相同SDK参数下，`minimal_schema`被API接受，`saved_full_schema`返回400 INVALID_ARGUMENT。结果文件为`schema-probe-20261114T131632Z-b620b8e9.json`（尚未上传，当前结论依据用户终端输出）。

这证明当前模型支持基本response_json_schema调用，而完整生成schema的形状/约束组合在这次诊断中不被接受。没有服务端字段详情，仍不能断言const、$ref或某一项数组边界是唯一根因。

## 本次修改

1. `gemini_agent.py`的API输出schema更新为`automatic-ref-schema-v2`。继续从现有`AutomaticSuggestion`生成，再投影成简单、内联的核心结构。保留type、properties、required、additionalProperties及enum；局部$ref展开成等价内联字段；const改为单值enum；移除API侧字符串长度和数组数量边界及title注解，降低约束组合复杂度。仍传response_json_schema，不退回仅提示词JSON模式，不迁移API。
2. 精确引用enum仍仅包含当前records，candidate_ref仍仅包含当前candidate_refs。有候选与无候选都采用同一结构；没有候选时不生成无效空enum。候选必须为空/完整且不重复，由后置规则检查。
3. **Pydantic/schema验收定义和`validate_agent_suggestion`未修改。** 空文字、1000字长度上限、每组数组数量上限、引用数量、未知引用、缺失/重复候选仍拒绝。API生成约束与应用验收规则分工不同，不是放宽验收标准。
4. `evaluate_water_faithfulness.py`补`api_status`和`api_message`，结构化说明最多4000字并遮盖当前密钥。仍不保存请求头或整个环境；错误后保存并停，不自动重试。
5. 两份测试随包更新：检查实际SDK请求携带内联结构与当前引用集合；新增回归确认API侧去掉长度/数量后应用层仍严格拒绝；确认错误说明保留且不泄露本地密钥。

[Google API参考](https://ai.google.dev/api/generate-content)说明JSON Schema仅支持子集；[Structured outputs](https://ai.google.dev/gemini-api/docs/structured-output)说明复杂约束可能被拒绝、部分限制需要应用层校验。本次是兼容化修正，不是证明某个单独关键字导致400的单变量实验。真实API是否接受新结构仍需验证。

居民原文、派生北京时间、自动检索、忠实性prompt、输入证据、模型、单次调用及已有数据库记录都沿用前一版。没有自动纠正UUID或宽松修补JSON，没有第二个模型或重构。

## 验证与下一步

本地相关44项测试通过（1.155秒），全量415项通过（11.635秒）。模拟HTTP测试不能证明Google已接受新结构；业务语义尚未通过。

包中5文件按相对路径覆盖。下一步先用原冻结基线调用一次真实模型，确认API兼容和结果；不重新导出、不录新数据，旧结果继续保留：

```bat
python evaluate_water_faithfulness.py replay --input "D:\ai_agent\project03\property-v2-evaluations\w1-baseline-20261114T125036Z-34b466ff.json" --runs 1
```

需在已经配置GEMINI_API_KEY及GEMINI_MODEL=gemini-3.5-flash-lite的同一终端运行。上传新w1-after文件：检查输出版本v2和输入摘要713b723bd3a9f8d763fdcbb3350591249852a6363f1106435e1864d0a88b497f，再审查引用和事实范围、冷热水、观察时间、未知、症状与原因。API接受或结构通过仍不能判语义通过；这一次合格后再补一次重复，保留两次输出。若仍400，读取新文件的完整错误，不无声去掉schema继续生成。

可选本地验证命令（此刻不要求额外展开一轮）：

```bat
python -m unittest tests.test_gemini_agent tests.test_agent_faithfulness tests.test_automatic_agent tests.test_agent_runner
```

预期44项。其他自然案例、网页保存/引用/刷新、仓库README/CI和简历保持原计划。
