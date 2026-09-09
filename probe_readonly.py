"""Explicit current-SDK/metadata probe. Does not create intents or notifications."""
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path

from broker import ReadOnlyBroker
from runtime import read_json
from support import build_plan,china_now


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,required=True)
    args=parser.parse_args()
    config=read_json(args.config)
    broker=ReadOnlyBroker(config)
    try:
        with redirect_stdout(io.StringIO()):
            broker.connect(); broker.ready()
            day=china_now().date().isoformat()
            trading=broker.is_trading_day(day)
            orders=broker.orders(); data=broker.ipos(); limits=broker.limits()
            samples=[]
            for code,item in data.items():
                try:
                    plan=build_plan(code,item,limits,day,['SH','SZ','KCB'])
                    samples.append({'type':plan.kind,'metadata_valid':True,'positive_quantity':plan.quantity>0})
                except Exception as exc:
                    samples.append({'metadata_valid':False,'error_type':type(exc).__name__,
                                    'reason':str(exc),'code':code,
                                    'ipo_data':{k:item.get(k) for k in ('type','purchaseDate','minPurchaseNum','maxPurchaseNum','issuePrice')}})
        print(json.dumps({'mode':'read_only','account_ready':True,'trading_day':trading,
                          'order_count':len(orders),'ipo_count':len(data),'metadata_checks':samples,
                          'submission_calls':0},ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'mode':'read_only','status':'failed','error_type':type(exc).__name__}))
        return 1
    finally:broker.close()


if __name__=='__main__':raise SystemExit(main())
