# 版本0.2代码审查

固定点为starter-baseline-20261010（ab7a2fb）。Standards与Spec按code-review指南独立审查1326ab6和b204ae2；后续0b1091e处理覆盖问题。范围为SQL兼容、日期/状态反馈、公开字段开关、CLI/cookbook传播和无凭据CI，未将未执行的模型A/B当作已经完成的实验。

## Standards

0项硬性违规，0项需要处理的Fowler smell；无新增P0/P1阻塞。状态反馈与保存轨迹一致；日期helper保留NULL和offset；字段检查opt-in并单独计时。SQL先记录成功，再做元数据检查；可恢复检查错误不撤销SQL，基础设施错误继续invalid。SQLPolicyError兼容ValueError。CI固定Actions SHA、只读权限，旧测试未削弱。

有范围的code-simplification检查未发现需要扩张重构之处，保留现有两种具体destination及工具事务入口；不为减少行数新增抽象。功能新增的I/O/日志开销有单独测量，未声称性能保持不变或有所提升。

## Spec

无本轮阻塞性问题；初次审查发现两项P3覆盖不足：

1. state只有成功语句数，不能判断已创建哪些模型。0b1091e从实际成功的SQL目标记录唯一created_models；重复执行仍保留语句数，不将名称或列覆盖当作值正确。
2. 字段反馈没有明确空表/重复调用回归。已新增空表字段齐全但终局R0，以及重复成功转换2条语句/1个模型且终局R1的测试。

Spec轴已复查0b1091e，确认两项P3闭合，无新增异常或副作用顺序回归。最终69项本地回归通过；没有以更改旧测试断言放行修复。

核心要求满足：新增函数仍完整遍历SQL AST，保留raw写入/cross-schema/catalog/I/O禁令；字段提示只读公开spec和实际LIMIT 0元数据；terminal reward未改；meta动作错误和infra错误分开；开关默认关闭并经CLI/recipe/builder传播。

模型A/B、更新后效果评估与正式教授提交仍属后续事项；当前本地和人工环境验证不替代它们。
