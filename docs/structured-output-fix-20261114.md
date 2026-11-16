# 两次真实复测失败：引用拼写与JSON语法

用户已完成前一包62项Windows测试，随后对固定W1输入调用Gemini两次；原始结果继续保留。第二份文件`w1-after-20261114T125927Z-41f65691.json`包含两次结果，上传为`Pasted text(20261114-130026).txt`。

| 运行 | 已确认原因 | 处理原则 |
|---|---|---|
| 1 | `EvidenceReferenceError`：W3的candidate_ref正确，但evidence_refs末尾误写为`927a6213a_AB8`，正确是`927a6213aab8` | 严格拒绝，不能猜测纠正编号 |
| 2 | `ValidationError`：两条候选的text之后多出引号，JSON不能解析 | 拒绝原输出，不用宽松JSON修补掩盖失败 |

冷水、北京时间和原文未知等表述有改善；第一条公共走廊判断改成值得结合排查，不再直接定性为单点故障。仅凭这些改善不能判整轮通过。第二条也不能因为人能读懂就算结构通过。两次仍是0/2可接受结果，不据两次推算一般失败率。

## 最小修正

上一版只设`response_mime_type="application/json"`，具体输出格式/引用拼写主要放在提示词中，没有向API发送完整JSON Schema。这是本次补齐的输出约束缺口，不归因于用户操作。

[Google官方Structured outputs文档](https://ai.google.dev/gemini-api/docs/structured-output)说明可以通过JSON Schema约束输出结构。当前已安装SDK的`GenerateContentConfig.response_json_schema`支持传入JSON Schema，本轮保持原generate_content API，不迁移到Interactions API。

- `gemini_agent.py`：从现有`AutomaticSuggestion`产生schema，通过`response_json_schema`发送给API；引用只能选当前records已有的键，候选编号只能选当前candidate_refs。没有候选时数组长度0，避免无效空enum；候选数量设置为本次预期数量。schema标识`automatic-ref-schema-v1`。
- 不更改上一包忠实性提示词、原文、时间派生、模型名、单次调用或重试设置。旧人工路径保持原行为。
- `evaluate_water_faithfulness.py`：评估文件新增输出schema版本和实际schema，便于核对配置；输入仍来自同一冻结基线。
- `tests/test_gemini_agent.py`：截获真实SDK序列化请求，验证schema、精确引用集合和候选约束被发送；无候选时仍有效；模拟服务忽略schema返回错引用/坏JSON时，后置校验仍失败且不修补、不重试、不保存为建议。

这是缩小格式和引用拼写错误机会，不是完整事实正确性保证。数组数量相同仍可能重复或漏掉某个候选，引用存在仍可能不支持正文，后置引用/覆盖校验和人工语义验收继续保留。API可能因schema复杂度或模型兼容性拒绝请求，不能静默降级无schema并冒称已约束；真实API接受与生成效果仍待用户复测。

## 下一步

包中4文件按相对路径覆盖前一包；不替换数据库或任何基线/after JSON。先执行：

```bat
python -m unittest tests.test_gemini_agent tests.test_agent_faithfulness tests.test_automatic_agent tests.test_agent_runner
```

预期42项通过。之后再指导同一冻结基线、同一模型两次真实复测，不重新录案例、不重查数据库，不改旧的失败文件。新的评估结果应包含`output_schema_version`与`response_json_schema`，仍逐项检查事实范围、冷热水、观察时间、症状/原因及未知信息，而不是只看结构通过。

本地42项通过；最终全量结果在交接记录中注明。SDK请求截获是模拟HTTP边界测试，不是Google真实约束解码效果或真实语义通过证明。
