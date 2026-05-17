# CCR 主观听音实验处理脚本

`ccr_analysis.py` 用于处理 CCR（Comparison Category Rating）主观听音实验数据，输入 N 名受试者对 M 对配对样本的原始 `-3..3` 评价，输出统计处理过程、循环三元组可靠性检测、配对 CMOS 统计、样本综合排序和主观评测结论。

当前主脚本已经支持“每个配对重复评测轮次”。例如 A、B、C 三个样本、比较 2 次时，每名受试者需要对 `A-B`、`A-C`、`B-C` 各评价 2 轮。循环三元组检测会先对同一受试者、同一配对的多轮评分求均值，再综合所有样本三元组判断评价一致性。

## 输入格式

推荐 CSV 长表格式如下：

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

旧版无 `round` 列的输入仍可使用，脚本会按每名受试者每个配对的出现顺序自动分配轮次。若每个配对只出现一次，可视为 1 轮评测。

CCR 标尺：

| 分数 | 含义 |
| --- | --- |
| 3 | Much Better |
| 2 | Better |
| 1 | Slightly Better |
| 0 | About the Same |
| -1 | Slightly Worse |
| -2 | Worse |
| -3 | Much Worse |

## 生成模板

10 名受试者、A/B/C 三个样本、每个配对比较 2 次：

```bash
python ccr_analysis.py --make-template ccr_rounds_template_3_samples_2_rounds.csv --template-subjects 10 --template-samples A,B,C --template-rounds 2
```

最多支持 5 个样本、5 个评测轮次：

```bash
python ccr_analysis.py --make-template ccr_rounds_template_5_samples_5_rounds.csv --template-subjects 10 --template-samples A,B,C,D,E --template-rounds 5
```

仓库中也保留了旧版 5 样本单轮模板 `ccr_input_template_5_samples.csv`，可继续作为单轮实验输入。

## 运行分析

如果实验设计要求每名受试者对每个配对比较 2 次：

```bash
python ccr_analysis.py ccr_rounds_template_3_samples_2_rounds.csv --rounds 2 --out-dir ccr_output
```

脚本会检查每名受试者每个配对是否恰好有 2 条评分。如果只想报告完整性问题而不中断分析：

```bash
python ccr_analysis.py data.csv --rounds 2 --allow-incomplete-rounds --out-dir ccr_output
```

常用输出文件：

- `processing_report.md`：完整统计处理过程和主观评测结论。
- `round_completeness.csv`：每名受试者每个配对的轮次完整性。
- `subject_reliability.csv`：循环三元组、Kendall zeta 与受试者可靠性结论。
- `circular_triads.csv`：循环三元组明细。
- `pair_summary.csv`：每对样本的 CMOS、置信区间、方向性符号检验、Holm 校正和结论。
- `sample_scores.csv`：基于最小二乘成对比较尺度的综合排序。
- `cleaned_scores.csv`：剔除不可靠受试者后的统一方向评分，保留 `round` 列便于追溯。

## 循环三元组阈值

脚本默认把以下情况判为受试者评价不可靠：

- `circular_triads / testable_triads > 0.25`
- 在完整且无并列的成对比较设计中，`Kendall zeta < 0.75`

多轮评测时，处理流程为：

1. 对同一受试者、同一配对的多轮 CCR 评分取均值。
2. 用均值判断配对方向，例如 `A>B`、`B>C`、`C>A`。
3. 综合所有样本三元组检测循环三元组。
4. 若循环比例或 Kendall zeta 超过阈值，则判定该受试者评价不可靠，并从最终统计中剔除。

可用内置验证样例检查该逻辑：

```bash
python ccr_analysis.py ccr_rounds_cycle_test.csv --rounds 2 --out-dir ccr_rounds_cycle_output
```

该样例中 `S02` 的两轮平均结果形成 `A>B, B>C, C>A`，应被判为不可靠。

可以按实验严格程度调整阈值：

```bash
python ccr_analysis.py data.csv --cycle-rate-threshold 0.2 --zeta-threshold 0.8
```

如果希望只报告不可靠受试者但不剔除：

```bash
python ccr_analysis.py data.csv --keep-unreliable
```

## 播放顺序重编码

若原始数据记录的是“第二个样本相对第一个样本”的评分：

```csv
subject,round,first_sample,second_sample,rating
S01,1,B,A,2
```

使用：

```bash
python ccr_analysis.py data.csv --mode presentation
```

若数据按 ITU-T P.800 Annex E 的参考/处理样本随机顺序记录：

```csv
subject,round,reference,processed,first_sample,second_sample,rating
S01,1,Ref,SysA,Ref,SysA,2
S02,1,Ref,SysA,SysA,Ref,-1
```

使用：

```bash
python ccr_analysis.py data.csv --mode p800
```

脚本会把结果统一重编码为“处理样本相对参考样本”的 CMOS。

## 约束

- `rating` 必须位于 `-3..3`。
- 默认最多支持 5 个待评测样本。
- 每个配对最多支持 5 个评测轮次。
- 使用 `--rounds` 指定实验设计轮次后，脚本会进行完整性检查。

## 方法说明

脚本参考 [ITU-T P.800](https://www.itu.int/rec/T-REC-P.800-199608-I) Annex E 的 CCR 标尺、CMOS 与播放顺序重编码原则；循环三元组一致性采用 [Kendall 与 Babington Smith 成对比较一致性](https://doi.org/10.1093/biomet/31.3-4.324) 思路；听音/声品质感官评估背景参考 [Sensory Evaluation of Sound](https://www.routledge.com/Sensory-Evaluation-of-Sound/Zacharov/p/book/9780367656744)。由于 CCR 评分属于有序分类数据，报告中同时给出工程上常用的均值 CMOS/置信区间和方向性符号检验。
