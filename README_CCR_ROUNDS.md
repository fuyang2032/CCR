# CCR 多轮配对评测脚本分支

这个分支版本在原 CCR 脚本基础上增加了“每个配对重复评测轮次”功能。适用于同一受试者对同一组样本对进行多次 CCR 比较的实验设计，例如每人对 `A-B`、`A-C`、`B-C` 都比较 2 次。

## 输入格式

推荐使用 CSV，每一行是一名受试者在某一轮中对一个配对样本的原始 CCR 评分：

```csv
subject,round,sample_a,sample_b,rating
S01,1,A,B,2
S01,1,A,C,1
S01,1,B,C,1
S01,2,A,B,1
S01,2,A,C,2
S01,2,B,C,1
```

字段含义：

- `subject`：受试者编号。
- `round`：该配对的评测轮次，范围 `1..5`。
- `sample_a`, `sample_b`：两两配对比较样本。
- `rating`：CCR 原始评分，范围 `-3..3`；正值表示 `sample_a` 优于 `sample_b`。

## 生成模板

10 名受试者、A/B/C 三个样本、每个配对比较 2 次：

```bash
python ccr_analysis_rounds.py --make-template ccr_rounds_template_3_samples_2_rounds.csv --template-subjects 10 --template-samples A,B,C --template-rounds 2
```

最多支持 5 个样本、5 个评测轮次：

```bash
python ccr_analysis_rounds.py --make-template ccr_rounds_template_5_samples_5_rounds.csv --template-subjects 10 --template-samples A,B,C,D,E --template-rounds 5
```

## 运行分析

如果实验设计要求每名受试者对每个配对比较 2 次：

```bash
python ccr_analysis_rounds.py ccr_rounds_template_3_samples_2_rounds.csv --rounds 2 --out-dir ccr_rounds_output
```

脚本会检查每名受试者每个配对是否恰好有 2 条评分。如果只想报告完整性问题而不中断分析：

```bash
python ccr_analysis_rounds.py data.csv --rounds 2 --allow-incomplete-rounds --out-dir ccr_rounds_output
```

## 循环三元组处理

多轮评测时，循环三元组按以下方式处理：

1. 对同一受试者、同一配对的多轮 CCR 评分取均值。
2. 用均值判断配对方向，例如 `A>B`、`B>C`、`C>A`。
3. 综合所有样本三元组检测循环三元组。
4. 若循环比例或 Kendall zeta 超过阈值，则判定该受试者评价不可靠，并从最终统计中剔除。

可用内置验证样例检查该逻辑：

```bash
python ccr_analysis_rounds.py ccr_rounds_cycle_test.csv --rounds 2 --out-dir ccr_rounds_cycle_output
```

该样例中 `S02` 的两轮平均结果形成 `A>B, B>C, C>A`，应被判为不可靠。

## 输出文件

- `round_completeness.csv`：每名受试者每个配对的轮次完整性。
- `subject_reliability.csv`：循环三元组、Kendall zeta 与受试者可靠性结论。
- `circular_triads.csv`：循环三元组明细。
- `pair_summary.csv`：配对 CMOS、置信区间、方向性检验与 Holm 校正结果。
- `sample_scores.csv`：样本综合 CCR 排序。
- `cleaned_scores.csv`：剔除不可靠受试者后的原始评分，保留 `round` 列便于追溯。
- `processing_report.md`：完整统计处理过程与主观评测结论。
