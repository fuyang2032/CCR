#!/usr/bin/env python3
"""
CCR (Comparison Category Rating) subjective listening-test analysis.

Default input format:
    subject,sample_a,sample_b,rating
    S01,A,B,2

`rating` is interpreted as the CCR score for sample_a compared with sample_b:
positive means sample_a is better than sample_b; negative means worse.

The script also supports:
    --mode presentation: rating is second_sample compared with first_sample.
    --mode p800: randomized P.800 order, recoded to processed compared with reference.

Outputs:
    - processing_report.md
    - subject_reliability.csv
    - circular_triads.csv
    - pair_summary.csv
    - sample_scores.csv
    - cleaned_scores.csv
"""

from __future__ import annotations

import argparse
import csv
import itertools
import math
import os
import statistics
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from statistics import NormalDist
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


CCR_LABELS = {
    3: "Much Better",
    2: "Better",
    1: "Slightly Better",
    0: "About the Same",
    -1: "Slightly Worse",
    -2: "Worse",
    -3: "Much Worse",
}


@dataclass(frozen=True)
class Observation:
    subject: str
    sample_a: str
    sample_b: str
    rating: float
    raw_row: int


@dataclass(frozen=True)
class CanonicalObservation:
    subject: str
    left: str
    right: str
    score: float
    raw_row: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Process CCR listening-test ratings, detect circular triads, "
            "exclude unreliable subjects, and report CMOS/pairwise conclusions."
        )
    )
    parser.add_argument("input_csv", help="Input CSV file.")
    parser.add_argument(
        "--out-dir",
        default="ccr_output",
        help="Directory for output CSV files and Markdown report.",
    )
    parser.add_argument(
        "--mode",
        choices=("pairwise", "presentation", "p800"),
        default="pairwise",
        help=(
            "pairwise: rating is sample_a compared with sample_b; "
            "presentation: rating is second_sample compared with first_sample; "
            "p800: recode randomized reference/processed presentation order."
        ),
    )
    parser.add_argument("--subject-col", default="subject")
    parser.add_argument("--sample-a-col", default="sample_a")
    parser.add_argument("--sample-b-col", default="sample_b")
    parser.add_argument("--first-col", default="first_sample")
    parser.add_argument("--second-col", default="second_sample")
    parser.add_argument("--reference-col", default="reference")
    parser.add_argument("--processed-col", default="processed")
    parser.add_argument("--rating-col", default="rating")
    parser.add_argument(
        "--min-abs-preference",
        type=float,
        default=1.0,
        help=(
            "Minimum absolute pair score counted as a decisive preference when "
            "detecting circular triads. Default 1.0 ignores only CCR=0 ties."
        ),
    )
    parser.add_argument(
        "--cycle-rate-threshold",
        type=float,
        default=0.25,
        help=(
            "Subject is unreliable when circular_triads / testable_triads "
            "is greater than this value. Used for incomplete or tied designs too."
        ),
    )
    parser.add_argument(
        "--zeta-threshold",
        type=float,
        default=0.75,
        help=(
            "For complete decisive pairwise designs, subject is unreliable when "
            "Kendall consistency zeta is below this value."
        ),
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=0.05,
        help="Significance level for confidence intervals and Holm-adjusted sign tests.",
    )
    parser.add_argument(
        "--keep-unreliable",
        action="store_true",
        help="Report unreliable subjects but keep their scores in the final summaries.",
    )
    return parser.parse_args()


def require_columns(fieldnames: Optional[Sequence[str]], required: Sequence[str]) -> None:
    if not fieldnames:
        raise ValueError("Input CSV has no header row.")
    missing = [col for col in required if col not in fieldnames]
    if missing:
        raise ValueError(f"Missing required column(s): {', '.join(missing)}")


def parse_rating(value: str, row_number: int) -> float:
    try:
        rating = float(value)
    except ValueError as exc:
        raise ValueError(f"Row {row_number}: rating is not numeric: {value!r}") from exc
    if rating < -3 or rating > 3:
        raise ValueError(f"Row {row_number}: CCR rating must be in [-3, 3], got {rating}")
    return rating


def read_observations(args: argparse.Namespace) -> List[Observation]:
    observations: List[Observation] = []
    with open(args.input_csv, newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
        reader = csv.DictReader(f, dialect=dialect)

        if args.mode == "pairwise":
            require_columns(
                reader.fieldnames,
                [args.subject_col, args.sample_a_col, args.sample_b_col, args.rating_col],
            )
        elif args.mode == "presentation":
            require_columns(
                reader.fieldnames,
                [args.subject_col, args.first_col, args.second_col, args.rating_col],
            )
        else:
            require_columns(
                reader.fieldnames,
                [
                    args.subject_col,
                    args.first_col,
                    args.second_col,
                    args.reference_col,
                    args.processed_col,
                    args.rating_col,
                ],
            )

        for row_number, row in enumerate(reader, start=2):
            subject = clean_cell(row.get(args.subject_col), row_number, args.subject_col)
            rating = parse_rating(row.get(args.rating_col, ""), row_number)

            if args.mode == "pairwise":
                sample_a = clean_cell(row.get(args.sample_a_col), row_number, args.sample_a_col)
                sample_b = clean_cell(row.get(args.sample_b_col), row_number, args.sample_b_col)
                score = rating
            elif args.mode == "presentation":
                first = clean_cell(row.get(args.first_col), row_number, args.first_col)
                second = clean_cell(row.get(args.second_col), row_number, args.second_col)
                sample_a = second
                sample_b = first
                score = rating
            else:
                first = clean_cell(row.get(args.first_col), row_number, args.first_col)
                second = clean_cell(row.get(args.second_col), row_number, args.second_col)
                reference = clean_cell(row.get(args.reference_col), row_number, args.reference_col)
                processed = clean_cell(row.get(args.processed_col), row_number, args.processed_col)

                if first == reference and second == processed:
                    score = rating
                elif first == processed and second == reference:
                    score = -rating
                else:
                    raise ValueError(
                        "Row "
                        f"{row_number}: first/second samples do not match reference/processed "
                        f"columns ({first!r}, {second!r}, {reference!r}, {processed!r})"
                    )
                sample_a = processed
                sample_b = reference

            if sample_a == sample_b:
                raise ValueError(f"Row {row_number}: sample_a and sample_b are identical.")
            observations.append(Observation(subject, sample_a, sample_b, score, row_number))
    if not observations:
        raise ValueError("Input CSV contains no data rows.")
    return observations


def clean_cell(value: Optional[str], row_number: int, column: str) -> str:
    if value is None:
        raise ValueError(f"Row {row_number}: missing value in column {column!r}")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"Row {row_number}: empty value in column {column!r}")
    return cleaned


def canonicalize(obs: Observation) -> CanonicalObservation:
    if obs.sample_a <= obs.sample_b:
        return CanonicalObservation(obs.subject, obs.sample_a, obs.sample_b, obs.rating, obs.raw_row)
    return CanonicalObservation(obs.subject, obs.sample_b, obs.sample_a, -obs.rating, obs.raw_row)


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def sample_sd(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    return statistics.stdev(values)


def median(values: Sequence[float]) -> float:
    return statistics.median(values) if values else float("nan")


def comb(n: int, k: int) -> int:
    if n < k:
        return 0
    return math.comb(n, k)


def kendall_tmax(n: int) -> int:
    if n < 3:
        return 0
    if n % 2 == 0:
        return n * (n * n - 4) // 24
    return n * (n * n - 1) // 24


def subject_pair_means(
    canonical: Sequence[CanonicalObservation],
) -> Dict[str, Dict[Tuple[str, str], float]]:
    values: Dict[str, Dict[Tuple[str, str], List[float]]] = defaultdict(lambda: defaultdict(list))
    for obs in canonical:
        values[obs.subject][(obs.left, obs.right)].append(obs.score)
    return {
        subject: {pair: mean(scores) for pair, scores in pairs.items()}
        for subject, pairs in values.items()
    }


def pair_direction(
    pair_scores: Dict[Tuple[str, str], float],
    a: str,
    b: str,
    min_abs_preference: float,
) -> Optional[int]:
    if a <= b:
        score = pair_scores.get((a, b))
    else:
        score = pair_scores.get((b, a))
        if score is not None:
            score = -score
    if score is None or abs(score) < min_abs_preference:
        return None
    return 1 if score > 0 else -1


def is_circular_triad(d_ab: int, d_bc: int, d_ca: int) -> bool:
    return (d_ab > 0 and d_bc > 0 and d_ca > 0) or (d_ab < 0 and d_bc < 0 and d_ca < 0)


def analyze_subject_reliability(
    observations: Sequence[Observation],
    canonical: Sequence[CanonicalObservation],
    min_abs_preference: float,
    cycle_rate_threshold: float,
    zeta_threshold: float,
) -> Tuple[List[dict], List[dict], set]:
    all_samples = sorted({obs.sample_a for obs in observations} | {obs.sample_b for obs in observations})
    pair_means = subject_pair_means(canonical)
    by_subject_rows: List[dict] = []
    circular_rows: List[dict] = []
    unreliable_subjects = set()

    for subject in sorted(pair_means):
        pair_scores = pair_means[subject]
        subject_samples = sorted(set(itertools.chain.from_iterable(pair_scores.keys())))
        n_samples = len(subject_samples)
        possible_pairs = comb(n_samples, 2)
        possible_triads = comb(n_samples, 3)
        decisive_pairs = sum(1 for score in pair_scores.values() if abs(score) >= min_abs_preference)

        testable_triads = 0
        circular_triads = 0
        for a, b, c in itertools.combinations(subject_samples, 3):
            d_ab = pair_direction(pair_scores, a, b, min_abs_preference)
            d_bc = pair_direction(pair_scores, b, c, min_abs_preference)
            d_ca = pair_direction(pair_scores, c, a, min_abs_preference)
            if d_ab is None or d_bc is None or d_ca is None:
                continue
            testable_triads += 1
            if is_circular_triad(d_ab, d_bc, d_ca):
                circular_triads += 1
                circular_rows.append(
                    {
                        "subject": subject,
                        "sample_1": a,
                        "sample_2": b,
                        "sample_3": c,
                        "relation_1": relation_text(a, b, d_ab),
                        "relation_2": relation_text(b, c, d_bc),
                        "relation_3": relation_text(c, a, d_ca),
                    }
                )

        cycle_rate = circular_triads / testable_triads if testable_triads else 0.0
        complete_decisive = decisive_pairs == possible_pairs and possible_pairs > 0
        tmax = kendall_tmax(n_samples)
        zeta = None
        if complete_decisive and tmax > 0:
            zeta = 1.0 - (circular_triads / tmax)

        unreliable = False
        reasons = []
        if testable_triads and cycle_rate > cycle_rate_threshold:
            unreliable = True
            reasons.append(f"cycle_rate>{cycle_rate_threshold:g}")
        if zeta is not None and zeta < zeta_threshold:
            unreliable = True
            reasons.append(f"zeta<{zeta_threshold:g}")
        if unreliable:
            unreliable_subjects.add(subject)

        by_subject_rows.append(
            {
                "subject": subject,
                "n_samples_seen": n_samples,
                "n_pairs_seen": len(pair_scores),
                "possible_pairs": possible_pairs,
                "decisive_pairs": decisive_pairs,
                "possible_triads": possible_triads,
                "testable_triads": testable_triads,
                "circular_triads": circular_triads,
                "cycle_rate": cycle_rate,
                "kendall_tmax_complete": tmax,
                "kendall_zeta_complete_decisive": zeta,
                "reliable": not unreliable,
                "rejection_reason": ";".join(reasons),
            }
        )

    observed_subjects = {obs.subject for obs in observations}
    missing_subjects = observed_subjects - set(pair_means)
    for subject in sorted(missing_subjects):
        by_subject_rows.append(
            {
                "subject": subject,
                "n_samples_seen": 0,
                "n_pairs_seen": 0,
                "possible_pairs": 0,
                "decisive_pairs": 0,
                "possible_triads": 0,
                "testable_triads": 0,
                "circular_triads": 0,
                "cycle_rate": 0.0,
                "kendall_tmax_complete": 0,
                "kendall_zeta_complete_decisive": None,
                "reliable": True,
                "rejection_reason": "",
            }
        )

    _ = all_samples
    return by_subject_rows, circular_rows, unreliable_subjects


def relation_text(a: str, b: str, direction: int) -> str:
    if direction > 0:
        return f"{a}>{b}"
    return f"{b}>{a}"


def binomial_two_sided_p(k_success: int, n: int) -> Optional[float]:
    if n <= 0:
        return None
    k = min(k_success, n - k_success)
    prob = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return min(1.0, 2.0 * prob)


def holm_adjust(p_values: Sequence[Optional[float]]) -> List[Optional[float]]:
    indexed = [(i, p) for i, p in enumerate(p_values) if p is not None]
    m = len(indexed)
    adjusted: List[Optional[float]] = [None] * len(p_values)
    prev = 0.0
    for rank, (idx, p_value) in enumerate(sorted(indexed, key=lambda x: x[1]), start=1):
        adj = min(1.0, (m - rank + 1) * p_value)
        adj = max(prev, adj)
        adjusted[idx] = adj
        prev = adj
    return adjusted


def summarize_pairs(
    canonical: Sequence[CanonicalObservation],
    excluded_subjects: set,
    keep_unreliable: bool,
    alpha: float,
) -> Tuple[List[dict], List[CanonicalObservation]]:
    clean = [
        obs
        for obs in canonical
        if keep_unreliable or obs.subject not in excluded_subjects
    ]
    grouped: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    for obs in clean:
        grouped[(obs.left, obs.right)].append(obs.score)

    z = NormalDist().inv_cdf(1.0 - alpha / 2.0)
    rows: List[dict] = []
    p_values: List[Optional[float]] = []
    for left, right in sorted(grouped):
        scores = grouped[(left, right)]
        n = len(scores)
        avg = mean(scores)
        sd = sample_sd(scores)
        se = sd / math.sqrt(n) if n else float("nan")
        ci_half = z * se if n > 1 else 0.0
        pos = sum(1 for s in scores if s > 0)
        neg = sum(1 for s in scores if s < 0)
        ties = sum(1 for s in scores if s == 0)
        decisive_n = pos + neg
        p_sign = binomial_two_sided_p(pos, decisive_n)
        p_values.append(p_sign)
        row = {
            "sample_left": left,
            "sample_right": right,
            "n": n,
            "mean_cmos_left_minus_right": avg,
            "median": median(scores),
            "sd": sd,
            "se": se,
            "ci_low": avg - ci_half,
            "ci_high": avg + ci_half,
            "positive_votes": pos,
            "negative_votes": neg,
            "tie_votes": ties,
            "sign_test_p": p_sign,
            "holm_p": None,
            "conclusion": "",
        }
        for score in range(-3, 4):
            row[f"count_{score}"] = sum(1 for s in scores if int(round(s)) == score)
        rows.append(row)

    adjusted = holm_adjust(p_values)
    for row, p_adj in zip(rows, adjusted):
        row["holm_p"] = p_adj
        row["conclusion"] = pair_conclusion(row, alpha)
    return rows, clean


def pair_conclusion(row: dict, alpha: float) -> str:
    left = row["sample_left"]
    right = row["sample_right"]
    avg = row["mean_cmos_left_minus_right"]
    ci_low = row["ci_low"]
    ci_high = row["ci_high"]
    p_adj = row["holm_p"]

    if row["n"] < 2:
        return "样本量不足，仅作描述"

    sign_ok = p_adj is not None and p_adj < alpha
    ci_positive = ci_low > 0
    ci_negative = ci_high < 0

    if avg > 0 and sign_ok and ci_positive:
        return f"{left} 显著优于 {right}"
    if avg < 0 and sign_ok and ci_negative:
        return f"{right} 显著优于 {left}"
    if avg > 0:
        return f"{left} 平均更优，但统计证据不足或不完全一致"
    if avg < 0:
        return f"{right} 平均更优，但统计证据不足或不完全一致"
    return "两样本平均无差异"


def solve_linear_system(matrix: List[List[float]], rhs: List[float]) -> List[float]:
    n = len(rhs)
    a = [row[:] + [rhs_i] for row, rhs_i in zip(matrix, rhs)]

    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError("Linear system is singular; check sample graph connectivity.")
        if pivot != col:
            a[col], a[pivot] = a[pivot], a[col]

        pivot_value = a[col][col]
        for j in range(col, n + 1):
            a[col][j] /= pivot_value

        for r in range(n):
            if r == col:
                continue
            factor = a[r][col]
            if abs(factor) < 1e-15:
                continue
            for j in range(col, n + 1):
                a[r][j] -= factor * a[col][j]

    return [a[i][n] for i in range(n)]


def connected_components(samples: Sequence[str], edges: Iterable[Tuple[str, str]]) -> List[List[str]]:
    adjacency: Dict[str, set] = {sample: set() for sample in samples}
    for a, b in edges:
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
    seen = set()
    components = []
    for sample in sorted(adjacency):
        if sample in seen:
            continue
        queue = deque([sample])
        seen.add(sample)
        component = []
        while queue:
            current = queue.popleft()
            component.append(current)
            for nxt in sorted(adjacency[current]):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        components.append(sorted(component))
    return components


def score_samples(clean: Sequence[CanonicalObservation]) -> List[dict]:
    samples = sorted({obs.left for obs in clean} | {obs.right for obs in clean})
    if not samples:
        return []
    edges = [(obs.left, obs.right) for obs in clean]
    components = connected_components(samples, edges)
    rows: List[dict] = []

    observations_by_component = []
    for component in components:
        component_set = set(component)
        component_obs = [
            obs
            for obs in clean
            if obs.left in component_set and obs.right in component_set
        ]
        observations_by_component.append((component, component_obs))

    for component_index, (component, component_obs) in enumerate(observations_by_component, start=1):
        k = len(component)
        if k == 1:
            rows.append(
                {
                    "component": component_index,
                    "rank": 1,
                    "sample": component[0],
                    "latent_ccr_score": 0.0,
                    "n_observations": 0,
                }
            )
            continue

        index = {sample: i for i, sample in enumerate(component)}
        size = k + 1
        matrix = [[0.0 for _ in range(size)] for _ in range(size)]
        rhs = [0.0 for _ in range(size)]

        for obs in component_obs:
            i = index[obs.left]
            j = index[obs.right]
            score = obs.score
            matrix[i][i] += 1.0
            matrix[j][j] += 1.0
            matrix[i][j] -= 1.0
            matrix[j][i] -= 1.0
            rhs[i] += score
            rhs[j] -= score

        for i in range(k):
            matrix[i][k] = 1.0
            matrix[k][i] = 1.0
        rhs[k] = 0.0

        solution = solve_linear_system(matrix, rhs)[:k]
        observation_count = defaultdict(int)
        for obs in component_obs:
            observation_count[obs.left] += 1
            observation_count[obs.right] += 1

        ordered = sorted(
            ((sample, solution[index[sample]]) for sample in component),
            key=lambda item: (-item[1], item[0]),
        )
        for rank, (sample, score) in enumerate(ordered, start=1):
            rows.append(
                {
                    "component": component_index,
                    "rank": rank,
                    "sample": sample,
                    "latent_ccr_score": score,
                    "n_observations": observation_count[sample],
                }
            )
    return rows


def write_csv(path: str, rows: Sequence[dict]) -> None:
    if not rows:
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write("")
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def fmt(value: object, digits: int = 3) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        return f"{value:.{digits}f}"
    return str(value)


def markdown_table(rows: Sequence[dict], columns: Sequence[Tuple[str, str]], limit: Optional[int] = None) -> str:
    if not rows:
        return "无数据\n"
    selected = list(rows[:limit] if limit else rows)
    header = "| " + " | ".join(title for _, title in columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in selected:
        body.append("| " + " | ".join(fmt(row.get(key)) for key, _ in columns) + " |")
    if limit and len(rows) > limit:
        body.append(f"| ... | 仅显示前 {limit} 行，共 {len(rows)} 行 |" + " |" * (len(columns) - 2))
    return "\n".join([header, sep] + body) + "\n"


def build_report(
    args: argparse.Namespace,
    observations: Sequence[Observation],
    reliability_rows: Sequence[dict],
    circular_rows: Sequence[dict],
    pair_rows: Sequence[dict],
    sample_rows: Sequence[dict],
    clean: Sequence[CanonicalObservation],
    unreliable_subjects: set,
) -> str:
    subjects = sorted({obs.subject for obs in observations})
    samples = sorted({obs.sample_a for obs in observations} | {obs.sample_b for obs in observations})
    raw_pairs = {
        tuple(sorted((obs.sample_a, obs.sample_b)))
        for obs in observations
    }
    retained_subjects = sorted({obs.subject for obs in clean})
    excluded_text = ", ".join(sorted(unreliable_subjects)) if unreliable_subjects else "无"
    kept_note = "是，仍纳入最终统计" if args.keep_unreliable else "否，已从最终统计中剔除"

    significant_pairs = [
        row
        for row in pair_rows
        if row["holm_p"] is not None
        and row["holm_p"] < args.alpha
        and (row["ci_low"] > 0 or row["ci_high"] < 0)
    ]
    top_rows = [row for row in sample_rows if row["rank"] == 1]
    top_text = ", ".join(
        f"{row['sample']} (component {row['component']}, score={fmt(row['latent_ccr_score'])})"
        for row in top_rows
    ) or "无"

    lines = [
        "# CCR 主观听音实验统计处理报告",
        "",
        "## 1. 输入与评分约定",
        "",
        f"- 输入文件：`{os.path.abspath(args.input_csv)}`",
        f"- 输入模式：`{args.mode}`",
        "- CCR 评分范围：-3..3；正值表示前述分析方向中的第一个样本优于第二个样本。",
        "- 0 分按“无明确偏好/大致相同”处理，不参与循环三元组方向判定。",
        f"- 原始评分条数：{len(observations)}",
        f"- 受试者数 N：{len(subjects)}",
        f"- 样本数：{len(samples)}",
        f"- 配对样本数 M：{len(raw_pairs)}",
        "",
        "CCR 标尺：3 Much Better, 2 Better, 1 Slightly Better, 0 About the Same, "
        "-1 Slightly Worse, -2 Worse, -3 Much Worse。",
        "",
        "## 2. 数据预处理",
        "",
        "- 检查受试者、样本名与评分列是否为空。",
        "- 检查评分是否落在 [-3, 3]。",
        "- 将所有配对统一到字典序方向 `(sample_left, sample_right)`；若原始方向相反，则评分取负。",
        "- `presentation` 模式把评分解释为第二个样本相对于第一个样本；`p800` 模式按参考/处理样本的播放顺序反向重编码。",
        "",
        "## 3. 循环三元组与受试者可靠性",
        "",
        "对每名受试者，将重复配对先取均值，再把绝对值不小于 "
        f"{args.min_abs_preference:g} 的配对视为有方向偏好。若存在 A>B、B>C、C>A "
        "或反向等价闭环，则记为一个循环三元组。",
        "",
        f"- 循环比例阈值：`circular_triads / testable_triads > {args.cycle_rate_threshold:g}`",
        f"- 完全且无并列的成对比较设计中，Kendall 一致性阈值：`zeta < {args.zeta_threshold:g}`",
        "- `zeta = 1 - T / Tmax`；T 为循环三元组数量，Tmax 为该样本数下最大可能循环三元组数量。",
        f"- 判为不可靠的受试者：{excluded_text}",
        f"- 不可靠受试者是否保留在最终统计：{kept_note}",
        "",
        markdown_table(
            reliability_rows,
            [
                ("subject", "受试者"),
                ("n_pairs_seen", "已评配对"),
                ("testable_triads", "可检三元组"),
                ("circular_triads", "循环数"),
                ("cycle_rate", "循环比例"),
                ("kendall_zeta_complete_decisive", "Kendall zeta"),
                ("reliable", "可靠"),
                ("rejection_reason", "原因"),
            ],
        ),
        "",
        "## 4. 有效数据统计",
        "",
        f"- 最终纳入受试者数：{len(retained_subjects)}",
        f"- 最终纳入评分条数：{len(clean)}",
        "",
        "每个配对输出 CMOS 均值、标准差、标准误、正态近似置信区间、方向性符号检验，以及 Holm 多重比较校正后的 p 值。"
        "由于 CCR 是有序分类评分，均值/置信区间用于工程解释，符号检验作为方向性稳健检验。",
        "",
        markdown_table(
            pair_rows,
            [
                ("sample_left", "样本 L"),
                ("sample_right", "样本 R"),
                ("n", "n"),
                ("mean_cmos_left_minus_right", "CMOS L-R"),
                ("ci_low", "CI低"),
                ("ci_high", "CI高"),
                ("positive_votes", "L胜"),
                ("negative_votes", "R胜"),
                ("tie_votes", "同等"),
                ("holm_p", "Holm p"),
                ("conclusion", "结论"),
            ],
        ),
        "",
        "## 5. 样本综合排序",
        "",
        "综合排序采用最小二乘成对比较尺度：拟合 `score(sample_i) - score(sample_j) ~= CCR(i,j)`，"
        "并在每个连通分量内令样本分数和为 0。因此分数只适合同一连通分量内比较。",
        "",
        markdown_table(
            sample_rows,
            [
                ("component", "分量"),
                ("rank", "排名"),
                ("sample", "样本"),
                ("latent_ccr_score", "综合CCR分"),
                ("n_observations", "相关评分数"),
            ],
        ),
        "",
        "## 6. 主观评测结论",
        "",
        f"- 综合排序第一的样本：{top_text}",
        f"- Holm 校正后达到显著方向差异的配对数：{len(significant_pairs)} / {len(pair_rows)}",
    ]

    if significant_pairs:
        lines.append("- 显著配对结论：")
        for row in significant_pairs:
            lines.append(
                f"  - {row['sample_left']} vs {row['sample_right']}: "
                f"{row['conclusion']} "
                f"(CMOS={fmt(row['mean_cmos_left_minus_right'])}, "
                f"95% CI=[{fmt(row['ci_low'])}, {fmt(row['ci_high'])}], "
                f"Holm p={fmt(row['holm_p'])})"
            )
    else:
        lines.append("- 未发现 Holm 校正后同时满足方向性符号检验和均值置信区间的显著配对差异。")

    if circular_rows:
        lines.extend(
            [
                "",
                "## 7. 循环三元组明细",
                "",
                markdown_table(
                    circular_rows,
                    [
                        ("subject", "受试者"),
                        ("sample_1", "样本1"),
                        ("sample_2", "样本2"),
                        ("sample_3", "样本3"),
                        ("relation_1", "关系1"),
                        ("relation_2", "关系2"),
                        ("relation_3", "关系3"),
                    ],
                    limit=50,
                ),
            ]
        )

    lines.extend(
        [
            "",
            "## 8. 方法依据",
            "",
            "- [ITU-T P.800](https://www.itu.int/rec/T-REC-P.800-199608-I) Annex E：CCR 采用 -3..3 七级比较标尺，输出 CMOS；随机播放顺序时需把处理样本先播放的试次取反后再统计。",
            "- [Kendall 与 Babington Smith, 1940](https://doi.org/10.1093/biomet/31.3-4.324) 成对比较一致性：用循环三元组数量 T 以及 Kendall consistency zeta 评估单个评价者内部一致性。",
            "- [Sensory Evaluation of Sound](https://www.routledge.com/Sensory-Evaluation-of-Sound/Zacharov/p/book/9780367656744) 总结了听音/声品质感官评估方法及单变量、多变量统计分析实践；CCR 均值适合作为工程汇总指标，但评分本质上是有序分类数据，因此结论同时报告非参数方向性检验。",
            "",
        ]
    )
    return "\n".join(lines)


def cleaned_score_rows(clean: Sequence[CanonicalObservation]) -> List[dict]:
    return [
        {
            "subject": obs.subject,
            "sample_left": obs.left,
            "sample_right": obs.right,
            "score_left_minus_right": obs.score,
            "raw_row": obs.raw_row,
        }
        for obs in clean
    ]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()
    try:
        observations = read_observations(args)
        canonical = [canonicalize(obs) for obs in observations]
        reliability_rows, circular_rows, unreliable_subjects = analyze_subject_reliability(
            observations,
            canonical,
            args.min_abs_preference,
            args.cycle_rate_threshold,
            args.zeta_threshold,
        )
        pair_rows, clean = summarize_pairs(
            canonical,
            unreliable_subjects,
            args.keep_unreliable,
            args.alpha,
        )
        sample_rows = score_samples(clean)

        os.makedirs(args.out_dir, exist_ok=True)
        write_csv(os.path.join(args.out_dir, "subject_reliability.csv"), reliability_rows)
        write_csv(os.path.join(args.out_dir, "circular_triads.csv"), circular_rows)
        write_csv(os.path.join(args.out_dir, "pair_summary.csv"), pair_rows)
        write_csv(os.path.join(args.out_dir, "sample_scores.csv"), sample_rows)
        write_csv(os.path.join(args.out_dir, "cleaned_scores.csv"), cleaned_score_rows(clean))

        report = build_report(
            args,
            observations,
            reliability_rows,
            circular_rows,
            pair_rows,
            sample_rows,
            clean,
            unreliable_subjects,
        )
        report_path = os.path.join(args.out_dir, "processing_report.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)

        print(report)
        print(f"\nOutput directory: {os.path.abspath(args.out_dir)}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
