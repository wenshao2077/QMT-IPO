"""Explicit calendar maintenance; no trading account, order or notification calls."""
import argparse
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path

from market_calendar import CalendarService, load_document, refresh_qmt, validate_document
from runtime import atomic_json, read_json, single_instance
from support import china_now


def import_annual(config, source, reviewed=False):
    if not reviewed:
        raise ValueError('Annual announcement transcription requires explicit --reviewed confirmation')
    doc = load_document(Path(source))
    validate_document(doc)
    if doc['source']['kind'] != 'exchange_annual':
        raise ValueError('Import accepts reviewed annual exchange packs only')
    state = Path(config['state_dir'])
    target = state/'calendars'/f"{doc['year']}.json"
    with single_instance(state/'calendar-write.lock'):
        if target.exists():
            # Preserve the exact old bytes as a local, non-delivery maintenance backup.
            import hashlib
            backup = state/'calendar-backups'/(target.stem+'-'+hashlib.sha256(target.read_bytes()).hexdigest()[:16]+'.json')
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                backup.write_bytes(target.read_bytes())
        atomic_json(target, doc)
    return {'status': 'imported', 'year': doc['year'], 'sha256': doc['sha256']}


def main(argv=None):
    p = argparse.ArgumentParser(description='交易日历状态、数据端更新和年度公告包导入；不连接交易账户')
    p.add_argument('action', choices=['status', 'coverage', 'refresh', 'import'])
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--config', type=Path)
    group.add_argument('--state-dir', type=Path)
    p.add_argument('--calendar-dir', type=Path)
    p.add_argument('--day', default=china_now().date().isoformat())
    p.add_argument('--annual-file', type=Path)
    p.add_argument('--reviewed', action='store_true')
    p.add_argument('--allow-download', action='store_true', help='明确允许SDK下载节假日，并按年度公告包核对未来列表')
    args = p.parse_args(argv)
    try:
        config = read_json(args.config) if args.config else {'state_dir': str(args.state_dir.resolve()), 'allowed_markets': ['SH', 'SZ']}
        if args.calendar_dir:
            config['calendar_dir'] = str(args.calendar_dir.resolve())
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            if args.action == 'status':
                result = CalendarService(config).decide(args.day).to_dict()
            elif args.action == 'coverage':
                from calendar_health import coverage_report
                result = coverage_report(config, args.day)
            elif args.action == 'import':
                if not args.annual_file:
                    raise ValueError('--annual-file is required')
                result = import_annual(config, args.annual_file, args.reviewed)
            else:
                result = refresh_qmt(config, args.day, allow_download=args.allow_download)
        print(json.dumps(result, ensure_ascii=False))
        return 2 if result.get('status') in ('unknown', 'blocked') else 0
    except Exception as exc:
        # Do not emit SDK URLs, local account identifiers or arbitrary exceptions.
        print(json.dumps({'status': 'calendar_update_failed', 'error_type': type(exc).__name__,
                          'remedy': '检查miniQMT数据端和SDK；核对年度公告包。原缓存不会因下载失败被清空。'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
