"""Write the skeleton of a daily digest (docs/poc/templates/daily-digest.md) from the board, the gates and worker reports.

    python tools/poc/orchestration/digest.py --board output/poc/board.json --gates output/poc/gates.json \\
        --reports output/poc/reports --day 3 --out output/poc/digests/day_03.md

The five sections of the template are filled with what the files already say: the packages that moved (status, branch,
verification state and the worker's reported status), the gate measurements copied from gates.json, the blocked packages
and failed reports, the packages to continue, and yes/no questions for the gates that wait for the owner. Scores and
everything else that needs judgement stay as placeholders for the commander. An existing digest is not overwritten
without ``--force``. The file is written with LF line ends.

Standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
POC = ROOT / 'output' / 'poc'
GATES = ('G1', 'G2', 'G3', 'G4', 'G5')
OWNER_GATES = ('G2', 'G3', 'G4', 'G5')
MAX_LINES = 3


class DigestError(Exception):
    pass


def read_json(path: Path, schema: str):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise DigestError(f'{path}: not found') from None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DigestError(f'{path}: not valid JSON ({exc})') from None
    except OSError as exc:
        raise DigestError(f'{path}: cannot read ({exc})') from None
    if not isinstance(data, dict) or data.get('schema_version') != schema:
        raise DigestError(f'{path}: not a {schema} file')
    return data


def read_reports(folder: Path):
    """Worker reports by package id; unreadable files are reported on stderr and skipped."""
    reports = {}
    folder = Path(folder)
    if not folder.is_dir():
        print(f'note: no reports folder {folder}', file=sys.stderr)
        return reports
    for path in sorted(folder.glob('*.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            print(f'note: skipped {path.name} ({exc})', file=sys.stderr)
            continue
        if not isinstance(data, dict) or data.get('schema_version') != 'worker.v1':
            print(f'note: skipped {path.name} (not worker.v1)', file=sys.stderr)
            continue
        reports[str(data.get('package') or path.stem)] = data
    return reports


def cell(text) -> str:
    return str(text).replace('|', '\\|').replace('\n', ' ')


def value_text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def render(board: dict, gates: dict, reports: dict, day: int, when: str) -> str:
    packages = board.get('packages') or {}
    lines = [f'# 日报 第 {day} 天（{when}）', '', '## 做了什么', '', '| 包 | 状态 | 分支 | 验证 |', '|---|---|---|---|']
    moved = [(pid, e) for pid, e in packages.items() if isinstance(e, dict) and e.get('status') != 'todo']
    for pid, entry in moved:
        status = entry.get('status')
        check = {'merged': '通过', 'verify': '待验证'}.get(status, '-')
        report = reports.get(pid)
        if report:
            check += f'（回报 {report.get("status")}）'
        lines.append(f'| {cell(pid)} | {cell(status)} | {cell(entry.get("branch") or "-")} | {cell(check)} |')
    if not moved:
        lines.append('| P<id> | merged / verify / in_progress / blocked | poc/<id> | 通过 / 退回：一句原因 |')

    lines += ['', '## 数字', '',
              '- 当天的分数：<pic> <总分>（L1 <x>/30，L2 <x>/30，L3 <x>/20，L4 <x>/20），边缘距离 <x> %']
    measured = [g for g in GATES if isinstance(gates.get(g), dict) and gates[g].get('measured_at')]
    for gate in measured:
        e = gates[gate]
        decided = f'，{e["decided_by"]} 定' if e.get('decided_by') else ''
        lines.append(f'- 关口：{gate} value {value_text(e.get("value"))}，threshold {e.get("threshold")}，'
                     f'证据 `{e.get("evidence_path")}`，状态 {e.get("status")}{decided}')
    if not measured:
        lines.append('- 关口：G<n> value <x>，threshold <y>，证据 `output/poc/<path>`')

    stuck = []
    for pid, entry in packages.items():
        if isinstance(entry, dict) and entry.get('status') == 'blocked':
            since = f'（{entry["blocked_from"]} 时卡住）' if entry.get('blocked_from') else ''
            stuck.append(f'- {pid}：{entry.get("note") or "<原因>"}{since}')
    for pid, report in reports.items():
        if report.get('status') in ('blocked', 'failed'):
            stuck.append(f'- {pid}：工人回报 {report.get("status")}，<一句原因>')
    lines += ['', '## 卡在哪', '']
    lines += stuck[:MAX_LINES] or ['- <一句话一条，最多三条>']
    if len(stuck) > MAX_LINES:
        lines.append(f'另有 {len(stuck) - MAX_LINES} 条，见 `board.py show`。')

    lines += ['', '## 明天做什么', '']
    for pid, entry in packages.items():
        if isinstance(entry, dict) and entry.get('status') in ('leased', 'in_progress', 'verify'):
            lines.append(f'- {pid}：继续（{entry["status"]}）')
    lines.append('- <包 id 和通道>')

    lines += ['', '## 需要决策人定什么（最多 3 个是/否问题）', '']
    waiting = [g for g in OWNER_GATES if g in measured and gates[g].get('status') == 'pending']
    for number, gate in enumerate(waiting[:MAX_LINES], 1):
        e = gates[gate]
        lines.append(f'{number}. {gate} 测得 {value_text(e.get("value"))}（阈值 {e.get("threshold")}，证据 '
                     f'`{e.get("evidence_path")}`），通过吗？')
    if not waiting:
        lines.append('1. <问题>？')
    return '\n'.join(lines) + '\n'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description='Write a daily digest skeleton (five sections of docs/poc/templates/daily-digest.md) from the board, '
                    'the gates and the worker reports; numbers that need judgement stay as placeholders.')
    parser.add_argument('--board', type=Path, default=POC / 'board.json', help='default: output/poc/board.json')
    parser.add_argument('--gates', type=Path, default=POC / 'gates.json', help='default: output/poc/gates.json')
    parser.add_argument('--reports', type=Path, default=POC / 'reports', help='default: output/poc/reports')
    parser.add_argument('--day', type=int, required=True, help='day number')
    parser.add_argument('--out', type=Path, required=True, help='e.g. output/poc/digests/day_03.md')
    parser.add_argument('--date', default=None, help='date in the title (default: today, YYYY-MM-DD)')
    parser.add_argument('--force', action='store_true', help='overwrite an existing digest')
    args = parser.parse_args(argv)
    try:
        if args.out.exists() and not args.force:
            raise DigestError(f'{args.out} exists; use --force to overwrite it')
        board = read_json(args.board, 'board.v1')
        gates = read_json(args.gates, 'gates.v1')
        text = render(board, gates, read_reports(args.reports), args.day, args.date or date.today().isoformat())
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
    except (DigestError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
    print(f'digest written: {args.out}')
    return 0


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    raise SystemExit(main())
