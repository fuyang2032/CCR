# CCR 多维度多轮评测与 Halo Effect 分析分支

本分支在当前 `ccr_analysis.py` 多轮 CCR 脚本基础上新增 `ccr_analysis_dimensions.py`，支持每个样本对在多个评测维度上分别打分，并对受试者是否存在 Halo Effect 倾向给出统计诊断。

## 输入格式

推荐 CSV 长表格式：

```csv
subject,dimension,round,sample_a,sample_b,rating
S01,Quality,1,A,B,2
S01,Quality,1,A,C,2
S01,Quality,1,B,C,1
S01,Quality,2,A,B,2
S01,Quality,2,A,C,2
S01,Quality,2,B,C,1
S01,Clarity,1,A,B,1
```

字段含义：

- `subject`：受试者编号。
- `dimension`：评测维度，例如 `Quality`, `Clarity`, `Naturalness`，最多 5 个维度。
- `round`：同一受试者、同一维度、同一配对的评测轮次，范围 `1..5`。
- `sample_a`, `sample_b`：两两配对比较样本。
- `rating`：CCR 原始评分，范围 `-3..3`；正值表示 `sample_a` 在该维度上优于 `sample_b`。

如果没有 `dimension` 列，脚本会把所有数据视作单一 `Overall` 维度，用于兼容旧输入。

## 生成模板

10 名受试者、A/B/C 三个样本、3 个维度、每个维度每个配对比较 2 次：

```bash
python ccr_analysis_dimensions.py --make-template ccr_dimensions_template_3_samples_3_dimensions_2_rounds.csv --template-subjects 10 --template-samples A,B,C --template-dimensions Quality,Clarity,Naturalness --template-rounds 2
```

最多支持 5 个维度和 5 个轮次：

```bash
python ccr_analysis_dimensions.py --make-template my_dimensions_input.csv --template-subjects 10 --template-samples A,B,C,D,E --template-dimensions D1,D2,D3,D4,D5 --template-rounds 5
```

## 运行分析

```bash
python ccr_analysis_dimensions.py ccr_dimensions_template_3_samples_3_dimensions_2_rounds.csv --rounds 2 --out-dir ccr_dimensions_output
```

脚本会检查每名受试者、每个维度、每个配对是否恰好有指定轮次的评分。如果只想报告完整性问题而不中断分析：

```bash
python ccr_analysis_dimensions.py data.csv --rounds 2 --allow-incomplete-rounds --out-dir ccr_dimensions_output
```

## 各维度独立统计

脚本会对每个维度分别输出：

- 轮次完整性检查。
- 循环三元组与受试者/维度可靠性。
- 每个样本对的 CMOS、置信区间、方向性符号检验与 Holm 校正。
- 每个维度内的样本综合 CCR 排序。

循环三元组处理方式：

1. 对同一受试者、同一维度、同一配对的多轮 CCR 评分取均值。
2. 用均值判断该维度下的配对方向，例如 `A>B`、`B>C`、`C>A`。
3. 在该维度内综合所有样本三元组检测循环三元组。
4. 若循环比例或 Kendall zeta 超过阈值，则判定该受试者在该维度的评价不可靠。

## Halo Effect 诊断

Halo Effect 指一个维度上的整体印象影响了其他维度评分，使多维度评分模式异常趋同。脚本采用以下统计线索诊断：

- 同一受试者不同维度配对评分向量的 Pearson 相关。
- 不同维度上配对偏好方向的一致率。
- 明确偏好配对占比。
- 群体层面的维度相似基线，用于避免把样本在多个维度上真实相近误判为个人 Halo。

主要输出指标：

- `raw_halo_index`：受试者个人跨维度评分模式相似度，越高表示越趋同。
- `group_baseline_halo_index`：群体层面各维度本身的相似度。
- `adjusted_halo_excess`：`max(0, raw_halo_index - group_baseline_halo_index)`。
- `halo_interpretation`：`high_possible_halo`, `moderate_possible_halo`, `low_or_not_detected` 等解释标签。

注意：Halo 指标是统计迹象，不能单独证明因果。若需要更严格的心理声学结论，建议结合随机化顺序、重复实验、访谈或显式总体偏好题目交叉验证。

可用内置样例验证：

```bash
python ccr_analysis_dimensions.py ccr_dimensions_halo_test.csv --rounds 2 --out-dir ccr_dimensions_halo_output
```

该样例中 `S01` 在所有维度上给出几乎完全相同的配对评分模式，应被标记为高 Halo 倾向。

## 输出文件

- `processing_report.md`：完整统计过程、各维度结论与 Halo 诊断。
- `dimension_round_completeness.csv`：受试者/维度/配对的轮次完整性。
- `dimension_subject_reliability.csv`：各受试者在各维度的循环三元组可靠性。
- `dimension_circular_triads.csv`：循环三元组明细。
- `dimension_pair_summary.csv`：各维度配对 CMOS 统计。
- `dimension_sample_scores.csv`：各维度样本综合排序。
- `halo_subject_summary.csv`：每名受试者的 Halo 指标。
- `halo_dimension_pairs.csv`：受试者维度对明细与群体基线。
- `cleaned_scores.csv`：剔除不可靠受试者/维度后的统一方向评分。
