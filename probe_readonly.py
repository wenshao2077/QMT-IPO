"""Explicit current-SDK/metadata probe. Does not create intents or notifications."""
import argparse
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path

from broker import ReadOnlyBroker
from market_calendar import CalendarService
from runtime import read_json, validate_config
from support import build_plan,china_now


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,required=True)
    args=parser.parse_args()
    config=validate_config(read_json(args.config))
    broker=ReadOnlyBroker(config)
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            broker.connect(); broker.ready()
            day=china_now().date().isoformat()
            calendar=CalendarService(config).decide(day).to_dict()
            orders=broker.orders(); data=broker.ipos(); limits=broker.limits()
            samples=[]
            for code,item in data.items():
                try:
                    plan=build_plan(code,item,limits,day,['SH','SZ','KCB'])
                    samples.append({'type':plan.kind,'metadata_valid':True,'positive_quantity':plan.quantity>0})
                except Exception as exc:
                    samples.append({'metadata_valid':False,'error_type':type(exc).__name__,
                                    'reason':'核对证券元数据与程序支持范围','code':code,
                                    'ipo_data':{k:item.get(k) for k in ('type','purchaseDate','minPurchaseNum','maxPurchaseNum','issuePrice')}})
        print(json.dumps({'mode':'read_only','account_ready':True,'calendar':calendar,
                          'order_count':len(orders),'ipo_count':len(data),'metadata_checks':samples,
                          'submission_calls':0},ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'mode':'read_only','status':'failed','error_type':type(exc).__name__}))
        return 1
    finally:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:broker.close()
            except Exception:pass


if __name__=='__main__':raise SystemExit(main())
