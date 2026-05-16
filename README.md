# CCR 主观听音实验处理脚本

`ccr_analysis.py` 用于处理 CCR（Comparison Category Rating）主观听音实验数据，输入 N 名受试者对 M 对样本的原始 `-3..3` 评分，输出统计处理过程、循环三元组可靠性检测、配对 CMOS 统计和主观评测结论。

## 输入格式

默认 CSV 列：

```csv
subject,sample_a,sample_b,rating
S01,A,B,2
S01,B,C,1
S01,C,A,1
S02,A,B,0
```

默认约定：`rating` 是 `sample_a` 相对于 `sample_b` 的 CCR 评分，正值表示 `sample_a` 更好，负值表示 `sample_a` 更差。

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

## 使用方法

```bash
python ccr_analysis.py data.csv --out-dir ccr_output
```

输出文件：

- `processing_report.md`：完整统计处理过程和主观评测结论
- `subject_reliability.csv`：每名受试者的循环三元组、一致性和可靠性判定
- `circular_triads.csv`：循环三元组明细
- `pair_summary.csv`：每对样本的 CMOS、置信区间、符号检验和结论
- `sample_scores.csv`：基于最小二乘成对比较尺度的综合排序
- `cleaned_scores.csv`：剔除不可靠受试者后的统一方向评分

## 推荐输入模板

仓库提供了一个可直接编辑的 5 样本模板：

```bash
python ccr_analysis.py ccr_input_template_5_samples.csv --out-dir ccr_output
```

模板格式仍然是当前脚本的长表格式：

```csv
subject,sample_a,sample_b,rating
S01,A,B,0
S01,A,C,0
S01,A,D,0
S01,A,E,0
S01,B,C,0
S01,B,D,0
S01,B,E,0
S01,C,D,0
S01,C,E,0
S01,D,E,0
```

填写规则：

- `subject`：受试者编号，例如 `S01`, `S02`, `P001`。
- `sample_a`, `sample_b`：被比较的两个样本。正值表示 `sample_a` 优于 `sample_b`。
- `rating`：CCR 原始评分，只能填写 `-3, -2, -1, 0, 1, 2, 3`。
- 5 个样本时，每名受试者需要 10 行：`AB, AC, AD, AE, BC, BD, BE, CD, CE, DE`。
- 4 个样本时，每名受试者保留 6 行：`AB, AC, AD, BC, BD, CD`，删除所有包含 `E` 的行。
- 3 个样本时，每名受试者保留 3 行：`AB, AC, BC`。
- 增加受试者：复制一个完整受试者块，并把 `subject` 改成新编号。
- 删除受试者：删除该受试者对应的所有配对行。

也可以用脚本生成新模板：

```bash
python ccr_analysis.py --make-template my_ccr_input.csv --template-subjects 12 --template-samples A,B,C,D
```

本脚本默认最多接受 5 个待评测样本；如果输入 CSV 中出现超过 5 个不同样本名，会报错提醒。

## 循环三元组阈值

脚本默认把以下情况判为受试者评价不可靠：

- `circular_triads / testable_triads > 0.25`
- 在完整且无并列的成对比较设计中，`Kendall zeta < 0.75`

可以按实验严格程度调整：

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
subject,first_sample,second_sample,rating
S01,B,A,2
```

使用：

```bash
python ccr_analysis.py data.csv --mode presentation
```

若数据按 ITU-T P.800 Annex E 的参考/处理样本随机顺序记录：

```csv
subject,reference,processed,first_sample,second_sample,rating
S01,Ref,SysA,Ref,SysA,2
S02,Ref,SysA,SysA,Ref,-1
```

使用：

```bash
python ccr_analysis.py data.csv --mode p800
```

脚本会把结果统一重编码为“处理样本相对参考样本”的 CMOS。

## 方法说明

脚本参考 [ITU-T P.800](https://www.itu.int/rec/T-REC-P.800-199608-I) Annex E 的 CCR 标尺、CMOS 与播放顺序重编码原则；循环三元组一致性采用 [Kendall 与 Babington Smith 成对比较一致性](https://doi.org/10.1093/biomet/31.3-4.324) 思路；听音/声品质感官评估背景参考 [Sensory Evaluation of Sound](https://www.routledge.com/Sensory-Evaluation-of-Sound/Zacharov/p/book/9780367656744)。由于 CCR 评分属于有序分类数据，报告中同时给出工程上常用的均值 CMOS/置信区间和方向性符号检验。
