"""Explicit read-only acceptance. No intents, outbox, config writes or submission."""
import argparse
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import re

from broker_factory import ReadOnlyBroker
from market_calendar import CalendarService
from privacy import redact
from runtime import read_json, validate_config
from support import build_plan, china_now


def probe(config, *, userdata=None, connection_only=False, broker_factory=ReadOnlyBroker, now=china_now):
    config = dict(validate_config(config))
    if userdata is not None:
        config['qmt_userdata'] = str(userdata)
        validate_config(config)
    broker = broker_factory(config)
    report = {'mode': 'read_only', 'submission_calls': 0, 'configuration_written': False,
              'message_sent': False, 'account_ready': False}
    stage = '连接QMT'
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            broker.connect()
            stage = '检查账户状态'
            broker.ready()
            report['account_ready'] = True
            if connection_only:
                report.update(status='read_only_connection_complete', checked_at=now().isoformat())
                return report
            day = now().date().isoformat()
            report['calendar'] = CalendarService(config).decide(day).to_dict()
            stage = '查询并核对券商委托'
            orders = broker.orders()
            if not isinstance(orders, list):
                raise RuntimeError('委托查询未返回完整列表')
            if any(row.get('day') != day or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', str(row.get('code', ''))) for row in orders):
                raise RuntimeError('委托日期或证券代码不符合合同')
            stage = '查询申购项目'
            data = broker.ipos()
            if not isinstance(data, dict):
                raise RuntimeError('申购数据不可用，不能当作无项目')
            stage = '查询申购额度'
            limits = broker.limits()
            if not isinstance(limits, dict):
                raise RuntimeError('申购额度数据不可用')
            samples = []
            for code, item in data.items():
                try:
                    plan = build_plan(code, item, limits, day, config['allowed_markets'])
                    samples.append({'type': plan.kind, 'metadata_valid': True, 'positive_quantity': plan.quantity > 0})
                except Exception as exc:
                    samples.append({'metadata_valid': False, 'error_type': type(exc).__name__})
            report.update(status='read_only_queries_complete', order_count=len(orders), ipo_count=len(data),
                          metadata_checks=samples, order_diagnostics=getattr(broker, 'order_diagnostics', {}))
    except Exception as exc:
        report.update(status='failed', error_stage=getattr(exc, 'stage', stage),
                      error_type=type(exc).__name__, error_detail=redact(str(exc), config)[:400])
        if hasattr(exc, 'diagnostic'):
            report['diagnostic'] = exc.diagnostic
    finally:
        report['connection'] = getattr(broker, 'connection_info', {})
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                broker.close()
            except Exception:
                report.update(status='failed', cleanup_error='sdk_connection_release_failed')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--qmt-userdata', type=Path, help='Explicit read-only endpoint override; never saved')
    parser.add_argument('--connection-only', action='store_true', help='Check connect/subscribe/account status only')
    args = parser.parse_args(argv)
    report = probe(read_json(args.config), userdata=args.qmt_userdata, connection_only=args.connection_only)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status'] in ('read_only_queries_complete', 'read_only_connection_complete') else 1


if __name__ == '__main__':
    raise SystemExit(main())
