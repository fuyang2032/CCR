#!/usr/bin/env python3
"""CCR analysis by dimension with repeated rounds and halo-effect diagnostics."""

import argparse
import csv
import itertools
import math
import os
import statistics
import sys
from collections import defaultdict

import ccr_analysis as base


MAX_DIMENSIONS = 5


def parse_args():
    parser = argparse.ArgumentParser(
        description="Analyze multi-dimensional CCR data and estimate possible Halo Effect."
    )
    parser.add_argument("input_csv", nargs="?")
    parser.add_argument("--out-dir", default="ccr_dimensions_output")
    parser.add_argument("--mode", choices=["pairwise", "presentation", "p800"], default="pairwise")
    parser.add_argument("--subject-col", default="subject")
    parser.add_argument("--dimension-col", default="dimension")
    parser.add_argument("--round-col", default="round")
    parser.add_argument("--sample-a-col", default="sample_a")
    parser.add_argument("--sample-b-col", default="sample_b")
    parser.add_argument("--first-col", default="first_sample")
    parser.add_argument("--second-col", default="second_sample")
    parser.add_argument("--reference-col", default="reference")
    parser.add_argument("--processed-col", default="processed")
    parser.add_argument("--rating-col", default="rating")
    parser.add_argument("--rounds", type=int)
    parser.add_argument("--max-rounds", type=int, default=base.MAX_ROUNDS)
    parser.add_argument("--max-samples", type=int, default=5)
    parser.add_argument("--max-dimensions", type=int, default=MAX_DIMENSIONS)
    parser.add_argument("--min-abs-preference", type=float, default=1.0)
    parser.add_argument("--cycle-rate-threshold", type=float, default=0.25)
    parser.add_argument("--zeta-threshold", type=float, default=0.75)
    parser.add_argument("--halo-high-threshold", type=float, default=0.80)
    parser.add_argument("--halo-moderate-threshold", type=float, default=0.60)
    parser.add_argument("--halo-excess-threshold", type=float, default=0.10)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--keep-unreliable", action="store_true")
    parser.add_argument("--allow-incomplete-rounds", action="store_true")
    parser.add_argument("--make-template", metavar="PATH")
    parser.add_argument("--template-subjects", type=int, default=10)
    parser.add_argument("--template-samples", default="A,B,C")
    parser.add_argument("--template-dimensions", default="Dimension1,Dimension2,Dimension3")
    parser.add_argument("--template-rounds", type=int, default=2)
    parser.add_argument("--template-fill", type=float, default=0.0)
    return parser.parse_args()


def parse_dimensions(raw):
    dims = [part.strip() for part in raw.split(",") if part.strip()]
    if not dims:
        raise ValueError("Template needs at least 1 dimension.")
    if len(dims) > MAX_DIMENSIONS:
        raise ValueError(f"Template supports at most {MAX_DIMENSIONS} dimensions.")
    if len(dims) != len(set(dims)):
        raise ValueError("Template dimension names must be unique.")
    return dims


def make_template(path, subjects, samples, dimensions, rounds, fill):
    if subjects < 1:
        raise ValueError("--template-subjects must be at least 1.")
    base.validate_round_value(rounds, "--template-rounds")
    if fill < -3 or fill > 3:
        raise ValueError("--template-fill must be in [-3, 3].")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    rating = str(int(fill)) if float(fill).is_integer() else str(fill)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["subject", "dimension", "round", "sample_a", "sample_b", "rating"])
        for idx in range(1, subjects + 1):
            subject = f"S{idx:02d}"
            for dim in dimensions:
                for round_idx in range(1, rounds + 1):
                    for a, b in itertools.combinations(samples, 2):
                        writer.writerow([subject, dim, round_idx, a, b, rating])


def require_input_columns(fieldnames, args):
    required = [args.subject_col, args.rating_col]
    if args.mode == "pairwise":
        required += [args.sample_a_col, args.sample_b_col]
    elif args.mode == "presentation":
        required += [args.first_col, args.second_col]
    else:
        required += [args.first_col, args.second_col, args.reference_col, args.processed_col]
    base.require_columns(fieldnames, required)


def canonicalize(subject, dimension, a, b, score, row_number, round_idx):
    if a <= b:
        return (subject, dimension, a, b, score, row_number, round_idx)
    return (subject, dimension, b, a, -score, row_number, round_idx)


def read_rows(args):
    rows = []
    with open(args.input_csv, newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        fieldnames = reader.fieldnames or []
        require_input_columns(fieldnames, args)
        has_dim = args.dimension_col in fieldnames
        has_round = args.round_col in fieldnames
        for row_number, row in enumerate(reader, start=2):
            subject = base.clean_cell(row.get(args.subject_col), row_number, args.subject_col)
            dimension = base.clean_cell(row.get(args.dimension_col), row_number, args.dimension_col) if has_dim else "Overall"
            round_idx = base.parse_round(row.get(args.round_col), row_number, args.round_col) if has_round else None
            rating = base.parse_rating(row.get(args.rating_col, ""), row_number)
            if args.mode == "pairwise":
                a = base.clean_cell(row.get(args.sample_a_col), row_number, args.sample_a_col)
                b = base.clean_cell(row.get(args.sample_b_col), row_number, args.sample_b_col)
                score = rating
            elif args.mode == "presentation":
                first = base.clean_cell(row.get(args.first_col), row_number, args.first_col)
                second = base.clean_cell(row.get(args.second_col), row_number, args.second_col)
                a, b, score = second, first, rating
            else:
                first = base.clean_cell(row.get(args.first_col), row_number, args.first_col)
                second = base.clean_cell(row.get(args.second_col), row_number, args.second_col)
                reference = base.clean_cell(row.get(args.reference_col), row_number, args.reference_col)
                processed = base.clean_cell(row.get(args.processed_col), row_number, args.processed_col)
                if first == reference and second == processed:
                    score = rating
                elif first == processed and second == reference:
                    score = -rating
                else:
                    raise ValueError(f"Row {row_number}: first/second samples do not match reference/processed columns.")
                a, b = processed, reference
            if a == b:
                raise ValueError(f"Row {row_number}: compared samples are identical.")
            rows.append(canonicalize(subject, dimension, a, b, score, row_number, round_idx))
    if not rows:
        raise ValueError("Input CSV contains no data rows.")
    return rows


def proxy_args(args):
    class Proxy:
        pass
    proxy = Proxy()
    proxy.max_rounds = args.max_rounds
    proxy.rounds = args.rounds
    proxy.max_samples = args.max_samples
    proxy.allow_incomplete_rounds = args.allow_incomplete_rounds
    return proxy


def base_rows(dim_rows):
    return [(s, a, b, score, row_num, round_idx) for s, dim, a, b, score, row_num, round_idx in dim_rows]


def dim_rows(dimension, rows):
    return [(s, dimension, a, b, score, row_num, round_idx) for s, a, b, score, row_num, round_idx in rows]


def analyze_by_dimension(rows, args):
    dimensions = sorted({row[1] for row in rows})
    samples = sorted({row[2] for row in rows} | {row[3] for row in rows})
    if len(dimensions) > min(args.max_dimensions, MAX_DIMENSIONS):
        raise ValueError(f"Input contains {len(dimensions)} dimensions, maximum is {min(args.max_dimensions, MAX_DIMENSIONS)}.")
    if len(samples) > args.max_samples:
        raise ValueError(f"Input contains {len(samples)} samples, maximum is {args.max_samples}.")

    completeness = []
    reliability = []
    circular = []
    pair_summary = []
    sample_scores = []
    clean = []
    unreliable = set()
    expected_rounds = args.rounds
    for dimension in dimensions:
        current = [row for row in rows if row[1] == dimension]
        current_base = base_rows(current)
        expected, comp = base.validate_design(current_base, proxy_args(args))
        expected_rounds = expected_rounds or expected
        for row in comp:
            completeness.append({"dimension": dimension, **row})
        rel, circ, bad_subjects = base.analyze_reliability(
            current_base,
            args.min_abs_preference,
            args.cycle_rate_threshold,
            args.zeta_threshold,
        )
        unreliable.update((subject, dimension) for subject in bad_subjects)
        for row in rel:
            reliability.append({"dimension": dimension, **row})
        for row in circ:
            circular.append({"dimension": dimension, **row})
        pairs, clean_base = base.summarize_pairs(current_base, bad_subjects, args.keep_unreliable, args.alpha)
        for row in pairs:
            pair_summary.append({"dimension": dimension, **row})
        for row in base.score_samples(clean_base):
            sample_scores.append({"dimension": dimension, **row})
        clean.extend(dim_rows(dimension, clean_base))
    return expected_rounds, completeness, reliability, circular, pair_summary, sample_scores, clean, unreliable


def pair_means_by_subject_dimension(rows):
    grouped = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for subject, dimension, a, b, score, row_num, round_idx in rows:
        grouped[subject][dimension][(a, b)].append(score)
    return {
        subject: {
            dimension: {pair: base.average(values) for pair, values in pairs.items()}
            for dimension, pairs in dims.items()
        }
        for subject, dims in grouped.items()
    }


def pearson(xs, ys):
    if len(xs) < 2:
        return None
    mx, my = base.average(xs), base.average(ys)
    vx = [x - mx for x in xs]
    vy = [y - my for y in ys]
    dx = math.sqrt(sum(x * x for x in vx))
    dy = math.sqrt(sum(y * y for y in vy))
    if dx == 0 or dy == 0:
        return None
    return sum(x * y for x, y in zip(vx, vy)) / (dx * dy)


def sign(value, threshold):
    if abs(value) < threshold:
        return 0
    return 1 if value > 0 else -1


def similarity_row(subject, dim_a, dim_b, profile_a, profile_b, threshold, baseline=False):
    pairs = sorted(set(profile_a) & set(profile_b))
    xs = [profile_a[pair] for pair in pairs]
    ys = [profile_b[pair] for pair in pairs]
    r = pearson(xs, ys)
    signs = [(sign(x, threshold), sign(y, threshold)) for x, y in zip(xs, ys)]
    decisive = [(a, b) for a, b in signs if a != 0 or b != 0]
    agree = [(a, b) for a, b in decisive if a == b and a != 0]
    sign_agreement = len(agree) / len(decisive) if decisive else None
    decisive_rate = len(decisive) / len(pairs) if pairs else 0.0
    components = []
    if r is not None:
        components.append(max(0.0, r))
    if sign_agreement is not None:
        components.append(sign_agreement)
    halo_pair_similarity = base.average(components) * decisive_rate if components else 0.0
    mean_abs_difference = base.average([abs(x - y) for x, y in zip(xs, ys)]) if pairs else None
    return {
        "subject": subject,
        "dimension_a": dim_a,
        "dimension_b": dim_b,
        "n_common_pairs": len(pairs),
        "pearson_r": r,
        "sign_agreement": sign_agreement,
        "decisive_rate": decisive_rate,
        "mean_abs_difference": mean_abs_difference,
        "halo_pair_similarity": halo_pair_similarity,
        "is_group_baseline": baseline,
    }


def classify_halo(raw, adjusted, args):
    if raw is None:
        return "insufficient_data"
    if raw >= args.halo_high_threshold and adjusted >= args.halo_excess_threshold:
        return "high_possible_halo"
    if raw >= args.halo_moderate_threshold and adjusted >= args.halo_excess_threshold:
        return "moderate_possible_halo"
    if raw >= args.halo_moderate_threshold:
        return "dimension_similarity_high_but_group_baseline_explains_much"
    return "low_or_not_detected"


def group_profiles(clean):
    grouped = defaultdict(lambda: defaultdict(list))
    for subject, dimension, a, b, score, row_num, round_idx in clean:
        grouped[dimension][(a, b)].append(score)
    return {
        dimension: {pair: base.average(values) for pair, values in pairs.items()}
        for dimension, pairs in grouped.items()
    }


def analyze_halo(rows, clean, unreliable, args):
    profiles = pair_means_by_subject_dimension(rows)
    baseline_profiles = group_profiles(clean)
    baseline_rows = [
        similarity_row("GROUP_BASELINE", a, b, baseline_profiles[a], baseline_profiles[b], args.min_abs_preference, True)
        for a, b in itertools.combinations(sorted(baseline_profiles), 2)
    ]
    group_index = base.average([row["halo_pair_similarity"] for row in baseline_rows]) if baseline_rows else 0.0

    detail = []
    summary = []
    for subject in sorted(profiles):
        dims = [d for d in sorted(profiles[subject]) if args.keep_unreliable or (subject, d) not in unreliable]
        rows_for_subject = []
        for a, b in itertools.combinations(dims, 2):
            row = similarity_row(subject, a, b, profiles[subject][a], profiles[subject][b], args.min_abs_preference)
            rows_for_subject.append(row)
            detail.append(row)
        raw = base.average([row["halo_pair_similarity"] for row in rows_for_subject]) if rows_for_subject else None
        adjusted = max(0.0, raw - group_index) if raw is not None else None
        avg_r = [row["pearson_r"] for row in rows_for_subject if row["pearson_r"] is not None]
        avg_sign = [row["sign_agreement"] for row in rows_for_subject if row["sign_agreement"] is not None]
        summary.append({
            "subject": subject,
            "reliable_dimensions_used": len(dims),
            "dimension_pair_count": len(rows_for_subject),
            "avg_inter_dimension_pearson_r": base.average(avg_r) if avg_r else None,
            "avg_direction_agreement": base.average(avg_sign) if avg_sign else None,
            "avg_decisive_rate": base.average([row["decisive_rate"] for row in rows_for_subject]) if rows_for_subject else None,
            "raw_halo_index": raw,
            "group_baseline_halo_index": group_index,
            "adjusted_halo_excess": adjusted,
            "halo_interpretation": classify_halo(raw, adjusted, args),
        })
    return summary, detail, baseline_rows, group_index


def cleaned_rows(clean):
    return [
        {
            "subject": s,
            "dimension": d,
            "round": round_idx,
            "sample_left": a,
            "sample_right": b,
            "score_left_minus_right": score,
            "raw_row": row_num,
        }
        for s, d, a, b, score, row_num, round_idx in clean
    ]


def build_report(args, rows, expected, completeness, reliability, circular, pair_summary, sample_scores, clean, unreliable, halo_summary, halo_detail, halo_baseline, group_index):
    subjects = sorted({row[0] for row in rows})
    dimensions = sorted({row[1] for row in rows})
    samples = sorted({row[2] for row in rows} | {row[3] for row in rows})
    high = [row["subject"] for row in halo_summary if row["halo_interpretation"] in {"high_possible_halo", "moderate_possible_halo"}]
    top = [f"{row['dimension']}: {row['sample']} (score={base.fmt(row['latent_ccr_score'])})" for row in sample_scores if row["rank"] == 1]
    significant = [
        row for row in pair_summary
        if row["holm_p"] is not None and row["holm_p"] < args.alpha and (row["ci_low"] > 0 or row["ci_high"] < 0)
    ]
    lines = [
        "# CCR 多维度多轮配对主观实验统计处理报告",
        "",
        "## 1. 输入与评分约定",
        "",
        f"- 输入文件：`{os.path.abspath(args.input_csv)}`",
        f"- 每个受试者、每个维度、每个配对目标轮次：{expected}",
        f"- 原始评分条数：{len(rows)}",
        f"- 受试者数 N：{len(subjects)}",
        f"- 维度数：{len(dimensions)} ({', '.join(dimensions)})",
        f"- 样本数：{len(samples)}",
        "- 正值表示 sample_a 在该维度上优于 sample_b。",
        "",
        "## 2. 轮次完整性检查",
        "",
        base.table(completeness, [
            ("subject", "受试者"), ("dimension", "维度"), ("sample_left", "样本L"),
            ("sample_right", "样本R"), ("expected_rounds", "期望轮次"),
            ("observed_rounds", "实际轮次"), ("rounds_seen", "轮次"), ("status", "状态"),
        ]),
        "",
        "## 3. 循环三元组与可靠性",
        "",
        f"- 判为不可靠的受试者/维度：{', '.join(f'{s}/{d}' for s, d in sorted(unreliable)) if unreliable else '无'}",
        "",
        base.table(reliability, [
            ("subject", "受试者"), ("dimension", "维度"), ("n_pairs_seen", "已评配对"),
            ("testable_triads", "可检三元组"), ("circular_triads", "循环数"),
            ("cycle_rate", "循环比例"), ("kendall_zeta_complete_decisive", "Kendall zeta"),
            ("reliable", "可靠"), ("rejection_reason", "原因"),
        ]),
        "",
        "## 4. 各维度配对统计",
        "",
        base.table(pair_summary, [
            ("dimension", "维度"), ("sample_left", "样本L"), ("sample_right", "样本R"),
            ("n", "n"), ("mean_cmos_left_minus_right", "CMOS L-R"),
            ("ci_low", "CI低"), ("ci_high", "CI高"), ("positive_votes", "L胜"),
            ("negative_votes", "R胜"), ("tie_votes", "同等"), ("holm_p", "Holm p"),
            ("conclusion", "结论"),
        ]),
        "",
        "## 5. 各维度样本排序",
        "",
        base.table(sample_scores, [
            ("dimension", "维度"), ("component", "分量"), ("rank", "排名"),
            ("sample", "样本"), ("latent_ccr_score", "综合CCR分"),
            ("n_observations", "相关评分数"),
        ]),
        "",
        "## 6. Halo Effect 诊断",
        "",
        "- Halo 指标衡量同一受试者不同维度配对评分模式的相似程度；它是统计迹象，不等于因果证明。",
        f"- 群体维度相似基线：{base.fmt(group_index)}",
        "",
        base.table(halo_summary, [
            ("subject", "受试者"), ("reliable_dimensions_used", "纳入维度"),
            ("dimension_pair_count", "维度对数"), ("avg_inter_dimension_pearson_r", "平均r"),
            ("avg_direction_agreement", "方向一致率"), ("avg_decisive_rate", "明确偏好率"),
            ("raw_halo_index", "Halo原始指数"), ("adjusted_halo_excess", "校正后Halo超额"),
            ("halo_interpretation", "解释"),
        ]),
        "",
        "### Halo 维度对明细",
        "",
        base.table(halo_detail + halo_baseline, [
            ("subject", "受试者"), ("dimension_a", "维度A"), ("dimension_b", "维度B"),
            ("n_common_pairs", "共同配对"), ("pearson_r", "r"),
            ("sign_agreement", "方向一致率"), ("decisive_rate", "明确偏好率"),
            ("mean_abs_difference", "平均绝对差"), ("halo_pair_similarity", "维度对相似度"),
            ("is_group_baseline", "群体基线"),
        ]),
        "",
        "## 7. 主观评测结论",
        "",
        f"- 各维度综合排序第一：{'; '.join(top) if top else '无'}",
        f"- Holm 校正后达到显著方向差异的配对数：{len(significant)} / {len(pair_summary)}",
        f"- 疑似中高 Halo Effect 受试者：{', '.join(high) if high else '无'}",
    ]
    if circular:
        lines.extend(["", "## 8. 循环三元组明细", "", base.table(circular, [
            ("subject", "受试者"), ("dimension", "维度"), ("sample_1", "样本1"),
            ("sample_2", "样本2"), ("sample_3", "样本3"),
            ("relation_1", "关系1"), ("relation_2", "关系2"), ("relation_3", "关系3"),
        ])])
    return "\n".join(lines)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args()
    try:
        if args.make_template:
            samples = base.parse_sample_names(args.template_samples)
            dimensions = parse_dimensions(args.template_dimensions)
            make_template(args.make_template, args.template_subjects, samples, dimensions, args.template_rounds, args.template_fill)
            rows = args.template_subjects * len(dimensions) * args.template_rounds * base.comb(len(samples), 2)
            print(f"Created template: {os.path.abspath(args.make_template)}")
            print(f"Subjects: {args.template_subjects}; samples: {', '.join(samples)}; dimensions: {', '.join(dimensions)}; rounds: {args.template_rounds}; rows: {rows}")
            return 0
        if not args.input_csv:
            raise ValueError("Please provide input_csv, or use --make-template PATH.")
        rows = read_rows(args)
        expected, completeness, reliability, circular, pair_summary, sample_scores, clean, unreliable = analyze_by_dimension(rows, args)
        halo_summary, halo_detail, halo_baseline, group_index = analyze_halo(rows, clean, unreliable, args)
        os.makedirs(args.out_dir, exist_ok=True)
        base.write_csv(os.path.join(args.out_dir, "dimension_round_completeness.csv"), completeness)
        base.write_csv(os.path.join(args.out_dir, "dimension_subject_reliability.csv"), reliability)
        base.write_csv(os.path.join(args.out_dir, "dimension_circular_triads.csv"), circular)
        base.write_csv(os.path.join(args.out_dir, "dimension_pair_summary.csv"), pair_summary)
        base.write_csv(os.path.join(args.out_dir, "dimension_sample_scores.csv"), sample_scores)
        base.write_csv(os.path.join(args.out_dir, "halo_subject_summary.csv"), halo_summary)
        base.write_csv(os.path.join(args.out_dir, "halo_dimension_pairs.csv"), halo_detail + halo_baseline)
        base.write_csv(os.path.join(args.out_dir, "cleaned_scores.csv"), cleaned_rows(clean))
        report = build_report(args, rows, expected, completeness, reliability, circular, pair_summary, sample_scores, clean, unreliable, halo_summary, halo_detail, halo_baseline, group_index)
        with open(os.path.join(args.out_dir, "processing_report.md"), "w", encoding="utf-8") as f:
            f.write(report)
        print(report)
        print("")
        print(f"Output directory: {os.path.abspath(args.out_dir)}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
