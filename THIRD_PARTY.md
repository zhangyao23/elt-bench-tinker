# 固定上游与许可

ELT-Bench：uiuc-kang-lab/ELT-Bench，elt_bench++ commit fcf3129df49ce5ce63ff46b2bbbbba1eaf9ec055。上游 README 声明 CC BY-SA 4.0。`elt_tinker/upstream/eva_stage2.py` 是原始文件的逐字副本，SHA-256 在验证记录中核验；仅复用 comparator/sort helper，不执行上游 cached CSV driver。官方 task/schema/evaluation SQL/GT 和源数据的来源与 SHA 在私有 runs/data 记录中保存。独立实现与文档采用 CC BY-SA 4.0，以保留相同的署名及 ShareAlike 条件；完整文本见 LICENSE。

Tinker cookbook：thinking-machines-lab/tinker-cookbook commit 1c03a20fdda98156ccbec15728c0e2764b5a0bc3，Apache-2.0。作为固定依赖安装，保留原源码许可证；工作副本/原始快照默认不加入本仓库。使用其 tools、renderer、EnvGroupBuilder、do_group_rollout、compute_advantages、assemble_training_data 和 train_step，未改上游实现。

数据来源：Hugging Face tttjjj/elt_bench revision 52a48541547a4340e19e54386350740acca80146（gt_snowflake 与 inputs_snowflake/shipping），以及固定官方 setup.sh/config.yaml 中的 Google Drive URLs。官方 CSV/Parquet/GT 不随实现仓库重分发。部分 archive URL 缺乏内容寻址版本；本地逐成员 SHA 固定已用字节，future preparation 应核对来源审计，不能认为 URL 不变就表示数据不变。

教授的任务原文、邮件、截图、私人材料及真实凭据不是本实现的公共文件。source fixtures 是独立生成的极小本地测试数据。
