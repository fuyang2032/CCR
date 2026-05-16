#!/usr/bin/env python3
'''
CCR subjective listening-test analysis.

Input CSV:
subject,sample_a,sample_b,rating
S01,A,B,2

Positive rating means sample_a is better than sample_b.
'''

import argparse
import csv
import itertools
import math
import os
import statistics
import sys
from collections import defaultdict, deque
from statistics import NormalDist


CCR_LABELS = {
    3: 'Much Better',
    2: 'Better',
    1: 'Slightly Better',
    0: 'About the Same',
    -1: 'Slightly Worse',
    -2: 'Worse',
    -3: 'Much Worse',
}


def parse_args():
    parser = argparse.ArgumentParser(
        description='Analyze CCR ratings, circular triads, CMOS, and sample ranking.'
    )
    parser.add_argument('input_csv', nargs='?', help='Input CSV file.')
    parser.add_argument('--out-dir', default='ccr_output')
    parser.add_argument('--mode', choices=['pairwise', 'presentation', 'p800'], default='pairwise')
    parser.add_argument('--subject-col', default='subject')
    parser.add_argument('--sample-a-col', default='sample_a')
    parser.add_argument('--sample-b-col', default='sample_b')
    parser.add_argument('--first-col', default='first_sample')
    parser.add_argument('--second-col', default='second_sample')
    parser.add_argument('--reference-col', default='reference')
    parser.add_argument('--processed-col', default='processed')
    parser.add_argument('--rating-col', default='rating')
    parser.add_argument('--min-abs-preference', type=float, default=1.0)
    parser.add_argument('--cycle-rate-threshold', type=float, default=0.25)
    parser.add_argument('--zeta-threshold', type=float, default=0.75)
    parser.add_argument('--alpha', type=float, default=0.05)
    parser.add_argument('--keep-unreliable', action='store_true')
    parser.add_argument('--max-samples', type=int, default=5)
    parser.add_argument('--make-template', metavar='PATH')
    parser.add_argument('--template-subjects', type=int, default=10)
    parser.add_argument('--template-samples', default='A,B,C,D,E')
    parser.add_argument('--template-fill', type=float, default=0.0)
    return parser.parse_args()


def parse_sample_names(raw):
    samples = [part.strip() for part in raw.split(',') if part.strip()]
    if len(samples) < 2:
        raise ValueError('Template needs at least 2 sample names.')
    if len(samples) > 5:
        raise ValueError('Template supports at most 5 sample names.')
    if len(samples) != len(set(samples)):
        raise ValueError('Template sample names must be unique.')
    return samples


def create_template(path, subject_count, samples, fill):
    if subject_count < 1:
        raise ValueError('--template-subjects must be at least 1.')
    if fill < -3 or fill > 3:
        raise ValueError('--template-fill must be in [-3, 3].')
    folder = os.path.dirname(os.path.abspath(path))
    if folder:
        os.makedirs(folder, exist_ok=True)
    rating = str(int(fill)) if float(fill).is_integer() else str(fill)
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['subject', 'sample_a', 'sample_b', 'rating'])
        pairs = list(itertools.combinations(samples, 2))
        for subject_index in range(1, subject_count + 1):
            subject = f'S{subject_index:02d}'
            for sample_a, sample_b in pairs:
                writer.writerow([subject, sample_a, sample_b, rating])


def clean_cell(value, row_number, column):
    if value is None or not value.strip():
        raise ValueError(f'Row {row_number}: empty value in column {column}.')
    return value.strip()


def parse_rating(value, row_number):
    try:
        rating = float(value)
    except ValueError as exc:
        raise ValueError(f'Row {row_number}: rating is not numeric: {value!r}') from exc
    if rating < -3 or rating > 3:
        raise ValueError(f'Row {row_number}: CCR rating must be in [-3, 3], got {rating}.')
    return rating


def require_columns(fieldnames, required):
    if not fieldnames:
        raise ValueError('Input CSV has no header row.')
    missing = [column for column in required if column not in fieldnames]
    if missing:
        missing_text = ', '.join(missing)
        raise ValueError(f'Missing required column(s): {missing_text}')


def read_observations(args):
    rows = []
    with open(args.input_csv, newline='', encoding='utf-8-sig') as f:
        sample = f.read(4096)
        f.seek(0)
        dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        if args.mode == 'pairwise':
            require_columns(reader.fieldnames, [args.subject_col, args.sample_a_col, args.sample_b_col, args.rating_col])
        elif args.mode == 'presentation':
            require_columns(reader.fieldnames, [args.subject_col, args.first_col, args.second_col, args.rating_col])
        else:
            require_columns(
                reader.fieldnames,
                [args.subject_col, args.first_col, args.second_col, args.reference_col, args.processed_col, args.rating_col],
            )
        for row_number, row in enumerate(reader, start=2):
            subject = clean_cell(row.get(args.subject_col), row_number, args.subject_col)
            rating = parse_rating(row.get(args.rating_col, ''), row_number)
            if args.mode == 'pairwise':
                sample_a = clean_cell(row.get(args.sample_a_col), row_number, args.sample_a_col)
                sample_b = clean_cell(row.get(args.sample_b_col), row_number, args.sample_b_col)
                score = rating
            elif args.mode == 'presentation':
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
                    raise ValueError(f'Row {row_number}: first/second samples do not match reference/processed columns.')
                sample_a = processed
                sample_b = reference
            if sample_a == sample_b:
                raise ValueError(f'Row {row_number}: sample_a and sample_b are identical.')
            rows.append((subject, sample_a, sample_b, score, row_number))
    if not rows:
        raise ValueError('Input CSV contains no data rows.')
    validate_sample_count(rows, args.max_samples)
    return rows


def validate_sample_count(rows, max_samples):
    if max_samples < 2:
        raise ValueError('--max-samples must be at least 2.')
    samples = sorted({row[1] for row in rows} | {row[2] for row in rows})
    if len(samples) > max_samples:
        sample_text = ', '.join(samples)
        raise ValueError(f'Input contains {len(samples)} samples ({sample_text}), but --max-samples is {max_samples}.')
    if len(samples) > 5:
        raise ValueError('This CCR workflow supports at most 5 test samples.')


def canonicalize(row):
    subject, sample_a, sample_b, score, row_number = row
    if sample_a <= sample_b:
        return (subject, sample_a, sample_b, score, row_number)
    return (subject, sample_b, sample_a, -score, row_number)


def average(values):
    return sum(values) / len(values) if values else float('nan')


def stdev(values):
    return statistics.stdev(values) if len(values) > 1 else 0.0


def comb(n, k):
    return math.comb(n, k) if n >= k else 0


def kendall_tmax(n):
    if n < 3:
        return 0
    if n % 2 == 0:
        return n * (n * n - 4) // 24
    return n * (n * n - 1) // 24


def subject_pair_means(canonical):
    values = defaultdict(lambda: defaultdict(list))
    for subject, left, right, score, row_number in canonical:
        values[subject][(left, right)].append(score)
    return {
        subject: {pair: average(scores) for pair, scores in pairs.items()}
        for subject, pairs in values.items()
    }


def pair_direction(pair_scores, a, b, min_abs_preference):
    if a <= b:
        score = pair_scores.get((a, b))
    else:
        score = pair_scores.get((b, a))
        if score is not None:
            score = -score
    if score is None or abs(score) < min_abs_preference:
        return None
    return 1 if score > 0 else -1


def is_cycle(d_ab, d_bc, d_ca):
    return (d_ab > 0 and d_bc > 0 and d_ca > 0) or (d_ab < 0 and d_bc < 0 and d_ca < 0)


def relation(a, b, direction):
    return f'{a}>{b}' if direction > 0 else f'{b}>{a}'


def analyze_reliability(rows, canonical, min_abs_preference, cycle_rate_threshold, zeta_threshold):
    pair_means = subject_pair_means(canonical)
    reliability = []
    circular = []
    unreliable = set()
    for subject in sorted(pair_means):
        pair_scores = pair_means[subject]
        samples = sorted(set(itertools.chain.from_iterable(pair_scores.keys())))
        possible_pairs = comb(len(samples), 2)
        possible_triads = comb(len(samples), 3)
        decisive_pairs = sum(1 for score in pair_scores.values() if abs(score) >= min_abs_preference)
        testable_triads = 0
        circular_triads = 0
        for a, b, c in itertools.combinations(samples, 3):
            d_ab = pair_direction(pair_scores, a, b, min_abs_preference)
            d_bc = pair_direction(pair_scores, b, c, min_abs_preference)
            d_ca = pair_direction(pair_scores, c, a, min_abs_preference)
            if d_ab is None or d_bc is None or d_ca is None:
                continue
            testable_triads += 1
            if is_cycle(d_ab, d_bc, d_ca):
                circular_triads += 1
                circular.append({
                    'subject': subject,
                    'sample_1': a,
                    'sample_2': b,
                    'sample_3': c,
                    'relation_1': relation(a, b, d_ab),
                    'relation_2': relation(b, c, d_bc),
                    'relation_3': relation(c, a, d_ca),
                })
        cycle_rate = circular_triads / testable_triads if testable_triads else 0.0
        complete_decisive = decisive_pairs == possible_pairs and possible_pairs > 0
        tmax = kendall_tmax(len(samples))
        zeta = None
        if complete_decisive and tmax > 0:
            zeta = 1.0 - circular_triads / tmax
        reasons = []
        if testable_triads and cycle_rate > cycle_rate_threshold:
            reasons.append(f'cycle_rate>{cycle_rate_threshold:g}')
        if zeta is not None and zeta < zeta_threshold:
            reasons.append(f'zeta<{zeta_threshold:g}')
        if reasons:
            unreliable.add(subject)
        reliability.append({
            'subject': subject,
            'n_samples_seen': len(samples),
            'n_pairs_seen': len(pair_scores),
            'possible_pairs': possible_pairs,
            'decisive_pairs': decisive_pairs,
            'possible_triads': possible_triads,
            'testable_triads': testable_triads,
            'circular_triads': circular_triads,
            'cycle_rate': cycle_rate,
            'kendall_tmax_complete': tmax,
            'kendall_zeta_complete_decisive': zeta,
            'reliable': not reasons,
            'rejection_reason': ';'.join(reasons),
        })
    return reliability, circular, unreliable


def sign_test_p(successes, n):
    if n <= 0:
        return None
    k = min(successes, n - successes)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2.0 * p)


def holm_adjust(p_values):
    indexed = [(i, p) for i, p in enumerate(p_values) if p is not None]
    adjusted = [None] * len(p_values)
    previous = 0.0
    total = len(indexed)
    for rank, item in enumerate(sorted(indexed, key=lambda x: x[1]), start=1):
        index, p_value = item
        value = min(1.0, (total - rank + 1) * p_value)
        value = max(previous, value)
        adjusted[index] = value
        previous = value
    return adjusted


def pair_conclusion(row, alpha):
    left = row['sample_left']
    right = row['sample_right']
    mean_value = row['mean_cmos_left_minus_right']
    p_value = row['holm_p']
    if row['n'] < 2:
        return '样本量不足，仅作描述'
    significant = p_value is not None and p_value < alpha
    if mean_value > 0 and significant and row['ci_low'] > 0:
        return f'{left} 显著优于 {right}'
    if mean_value < 0 and significant and row['ci_high'] < 0:
        return f'{right} 显著优于 {left}'
    if mean_value > 0:
        return f'{left} 平均更优，但统计证据不足或不完全一致'
    if mean_value < 0:
        return f'{right} 平均更优，但统计证据不足或不完全一致'
    return '两样本平均无差异'


def summarize_pairs(canonical, unreliable, keep_unreliable, alpha):
    clean = [row for row in canonical if keep_unreliable or row[0] not in unreliable]
    grouped = defaultdict(list)
    for subject, left, right, score, row_number in clean:
        grouped[(left, right)].append(score)
    z = NormalDist().inv_cdf(1.0 - alpha / 2.0)
    rows = []
    p_values = []
    for left, right in sorted(grouped):
        scores = grouped[(left, right)]
        count = len(scores)
        mean_value = average(scores)
        sd = stdev(scores)
        se = sd / math.sqrt(count) if count else float('nan')
        ci_half = z * se if count > 1 else 0.0
        positive = sum(1 for score in scores if score > 0)
        negative = sum(1 for score in scores if score < 0)
        ties = sum(1 for score in scores if score == 0)
        p_value = sign_test_p(positive, positive + negative)
        p_values.append(p_value)
        row = {
            'sample_left': left,
            'sample_right': right,
            'n': count,
            'mean_cmos_left_minus_right': mean_value,
            'median': statistics.median(scores),
            'sd': sd,
            'se': se,
            'ci_low': mean_value - ci_half,
            'ci_high': mean_value + ci_half,
            'positive_votes': positive,
            'negative_votes': negative,
            'tie_votes': ties,
            'sign_test_p': p_value,
            'holm_p': None,
            'conclusion': '',
        }
        for score in range(-3, 4):
            row[f'count_{score}'] = sum(1 for item in scores if int(round(item)) == score)
        rows.append(row)
    for row, adjusted in zip(rows, holm_adjust(p_values)):
        row['holm_p'] = adjusted
        row['conclusion'] = pair_conclusion(row, alpha)
    return rows, clean


def solve_linear(matrix, rhs):
    n = len(rhs)
    a = [row[:] + [value] for row, value in zip(matrix, rhs)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(a[row][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError('Linear system is singular; check sample graph connectivity.')
        if pivot != col:
            a[col], a[pivot] = a[pivot], a[col]
        factor = a[col][col]
        for j in range(col, n + 1):
            a[col][j] /= factor
        for row in range(n):
            if row == col:
                continue
            factor = a[row][col]
            for j in range(col, n + 1):
                a[row][j] -= factor * a[col][j]
    return [a[i][n] for i in range(n)]


def connected_components(samples, edges):
    graph = {sample: set() for sample in samples}
    for left, right in edges:
        graph.setdefault(left, set()).add(right)
        graph.setdefault(right, set()).add(left)
    seen = set()
    parts = []
    for sample in sorted(graph):
        if sample in seen:
            continue
        queue = deque([sample])
        seen.add(sample)
        part = []
        while queue:
            current = queue.popleft()
            part.append(current)
            for item in sorted(graph[current]):
                if item not in seen:
                    seen.add(item)
                    queue.append(item)
        parts.append(sorted(part))
    return parts


def score_samples(clean):
    samples = sorted({row[1] for row in clean} | {row[2] for row in clean})
    if not samples:
        return []
    rows = []
    for component_index, component in enumerate(connected_components(samples, [(row[1], row[2]) for row in clean]), start=1):
        indexes = {sample: i for i, sample in enumerate(component)}
        k = len(component)
        if k == 1:
            rows.append({'component': component_index, 'rank': 1, 'sample': component[0], 'latent_ccr_score': 0.0, 'n_observations': 0})
            continue
        matrix = [[0.0 for _ in range(k + 1)] for _ in range(k + 1)]
        rhs = [0.0 for _ in range(k + 1)]
        counts = defaultdict(int)
        for subject, left, right, score, row_number in clean:
            if left not in indexes or right not in indexes:
                continue
            i = indexes[left]
            j = indexes[right]
            matrix[i][i] += 1
            matrix[j][j] += 1
            matrix[i][j] -= 1
            matrix[j][i] -= 1
            rhs[i] += score
            rhs[j] -= score
            counts[left] += 1
            counts[right] += 1
        for i in range(k):
            matrix[i][k] = 1
            matrix[k][i] = 1
        solution = solve_linear(matrix, rhs)[:k]
        ordered = sorted(((sample, solution[indexes[sample]]) for sample in component), key=lambda item: (-item[1], item[0]))
        for rank, item in enumerate(ordered, start=1):
            sample, value = item
            rows.append({'component': component_index, 'rank': rank, 'sample': sample, 'latent_ccr_score': value, 'n_observations': counts[sample]})
    return rows


def write_csv(path, rows):
    if not rows:
        with open(path, 'w', encoding='utf-8') as f:
            f.write('')
        return
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def fmt(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return '是' if value else '否'
    if isinstance(value, float):
        if math.isnan(value):
            return ''
        return f'{value:.3f}'
    return str(value)


def table(rows, columns):
    if not rows:
        return '无数据'
    lines = []
    lines.append('| ' + ' | '.join(title for key, title in columns) + ' |')
    lines.append('| ' + ' | '.join('---' for item in columns) + ' |')
    for row in rows:
        lines.append('| ' + ' | '.join(fmt(row.get(key)) for key, title in columns) + ' |')
    return chr(10).join(lines)


def build_report(args, rows, reliability, circular, pair_rows, sample_rows, clean, unreliable):
    subjects = sorted({row[0] for row in rows})
    samples = sorted({row[1] for row in rows} | {row[2] for row in rows})
    pairs = {tuple(sorted((row[1], row[2]))) for row in rows}
    significant = [
        row for row in pair_rows
        if row['holm_p'] is not None and row['holm_p'] < args.alpha and (row['ci_low'] > 0 or row['ci_high'] < 0)
    ]
    top = [row for row in sample_rows if row['rank'] == 1]
    top_parts = []
    for row in top:
        sample = row.get('sample')
        component = row.get('component')
        score = fmt(row.get('latent_ccr_score'))
        top_parts.append(f'{sample} (component {component}, score={score})')
    top_text = ', '.join(top_parts) or '无'
    lines = [
        '# CCR 主观听音实验统计处理报告',
        '',
        '## 1. 输入与评分约定',
        '',
        f'- 输入文件：`{os.path.abspath(args.input_csv)}`',
        f'- 输入模式：`{args.mode}`',
        '- CCR 评分范围：-3..3；正值表示 sample_a 优于 sample_b。',
        '- 0 分按无明确偏好处理，不参与循环三元组方向判定。',
        f'- 原始评分条数：{len(rows)}',
        f'- 受试者数 N：{len(subjects)}',
        f'- 样本数：{len(samples)}',
        f'- 配对样本数 M：{len(pairs)}',
        '',
        '## 2. 数据预处理',
        '',
        '- 检查受试者、样本名与评分列是否为空。',
        '- 检查评分是否落在 [-3, 3]。',
        '- 将所有配对统一到字典序方向；若原始方向相反，则评分取负。',
        f'- 最多支持样本数：{args.max_samples}。',
        '',
        '## 3. 循环三元组与受试者可靠性',
        '',
        f'- 循环比例阈值：`circular_triads / testable_triads > {args.cycle_rate_threshold:g}`',
        f'- Kendall 一致性阈值：`zeta < {args.zeta_threshold:g}`',
        f'- 判为不可靠的受试者：{unreliable_text(unreliable)}',
        '',
        table(reliability, [
            ('subject', '受试者'),
            ('n_pairs_seen', '已评配对'),
            ('testable_triads', '可检三元组'),
            ('circular_triads', '循环数'),
            ('cycle_rate', '循环比例'),
            ('kendall_zeta_complete_decisive', 'Kendall zeta'),
            ('reliable', '可靠'),
            ('rejection_reason', '原因'),
        ]),
        '',
        '## 4. 有效数据统计',
        '',
        f'- 最终纳入受试者数：{len(sorted({row[0] for row in clean}))}',
        f'- 最终纳入评分条数：{len(clean)}',
        '',
        table(pair_rows, [
            ('sample_left', '样本 L'),
            ('sample_right', '样本 R'),
            ('n', 'n'),
            ('mean_cmos_left_minus_right', 'CMOS L-R'),
            ('ci_low', 'CI低'),
            ('ci_high', 'CI高'),
            ('positive_votes', 'L胜'),
            ('negative_votes', 'R胜'),
            ('tie_votes', '同等'),
            ('holm_p', 'Holm p'),
            ('conclusion', '结论'),
        ]),
        '',
        '## 5. 样本综合排序',
        '',
        table(sample_rows, [
            ('component', '分量'),
            ('rank', '排名'),
            ('sample', '样本'),
            ('latent_ccr_score', '综合CCR分'),
            ('n_observations', '相关评分数'),
        ]),
        '',
        '## 6. 主观评测结论',
        '',
        f'- 综合排序第一的样本：{top_text}',
        f'- Holm 校正后达到显著方向差异的配对数：{len(significant)} / {len(pair_rows)}',
    ]
    if significant:
        lines.append('- 显著配对结论：')
        for row in significant:
            left = row.get('sample_left')
            right = row.get('sample_right')
            conclusion = row.get('conclusion')
            lines.append(f'  - {left} vs {right}: {conclusion}')
    else:
        lines.append('- 未发现 Holm 校正后同时满足方向性符号检验和均值置信区间的显著配对差异。')
    if circular:
        lines.extend([
            '',
            '## 7. 循环三元组明细',
            '',
            table(circular, [
                ('subject', '受试者'),
                ('sample_1', '样本1'),
                ('sample_2', '样本2'),
                ('sample_3', '样本3'),
                ('relation_1', '关系1'),
                ('relation_2', '关系2'),
                ('relation_3', '关系3'),
            ]),
        ])
    lines.extend([
        '',
        '## 8. 方法依据',
        '',
        '- ITU-T P.800 Annex E：CCR 采用 -3..3 七级比较标尺，输出 CMOS。',
        '- Kendall 与 Babington Smith 成对比较一致性：用循环三元组数量和 zeta 评估评价者内部一致性。',
        '- CCR 评分属于有序分类数据，因此同时报告均值 CMOS 和方向性符号检验。',
    ])
    return chr(10).join(lines)


def cleaned_rows(clean):
    return [
        {'subject': subject, 'sample_left': left, 'sample_right': right, 'score_left_minus_right': score, 'raw_row': row_number}
        for subject, left, right, score, row_number in clean
    ]


def unreliable_text(unreliable):
    return ', '.join(sorted(unreliable)) if unreliable else '无'


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
    args = parse_args()
    try:
        if args.make_template:
            samples = parse_sample_names(args.template_samples)
            create_template(args.make_template, args.template_subjects, samples, args.template_fill)
            row_count = args.template_subjects * comb(len(samples), 2)
            print(f'Created template: {os.path.abspath(args.make_template)}')
            sample_text = ', '.join(samples)
            print(f'Subjects: {args.template_subjects}; samples: {sample_text}; rows: {row_count}')
            return 0
        if not args.input_csv:
            raise ValueError('Please provide input_csv, or use --make-template PATH.')
        rows = read_observations(args)
        canonical = [canonicalize(row) for row in rows]
        reliability, circular, unreliable = analyze_reliability(
            rows,
            canonical,
            args.min_abs_preference,
            args.cycle_rate_threshold,
            args.zeta_threshold,
        )
        pair_rows, clean = summarize_pairs(canonical, unreliable, args.keep_unreliable, args.alpha)
        sample_rows = score_samples(clean)
        os.makedirs(args.out_dir, exist_ok=True)
        write_csv(os.path.join(args.out_dir, 'subject_reliability.csv'), reliability)
        write_csv(os.path.join(args.out_dir, 'circular_triads.csv'), circular)
        write_csv(os.path.join(args.out_dir, 'pair_summary.csv'), pair_rows)
        write_csv(os.path.join(args.out_dir, 'sample_scores.csv'), sample_rows)
        write_csv(os.path.join(args.out_dir, 'cleaned_scores.csv'), cleaned_rows(clean))
        report = build_report(args, rows, reliability, circular, pair_rows, sample_rows, clean, unreliable)
        report_path = os.path.join(args.out_dir, 'processing_report.md')
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)
        print(report)
        print('')
        print(f'Output directory: {os.path.abspath(args.out_dir)}')
        return 0
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
