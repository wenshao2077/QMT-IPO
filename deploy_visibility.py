"""Install this narrowly scoped update under the existing ledger lock. No orders/tasks."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--staging',type=Path,required=True)
    args=parser.parse_args();root=args.root.resolve();staging=args.staging.resolve()
    if root not in staging.parents:raise ValueError('Staging must be inside this application')
    sys.path.insert(0,str(root/'code'))
    from runtime import read_json,single_instance,atomic_json
    config_path=root/'config.json';config=read_json(config_path)
    before=hashlib.sha256(config_path.read_bytes()).hexdigest()
    names=['run_history.py','coordinator.py','app.py','panel_backend.py','panel.py','test_system.py','test_panel.py','test_run_history.py']
    for name in names:
        if not (staging/name).is_file():raise FileNotFoundError(name)
        compile((staging/name).read_text(encoding='utf-8'),name,'exec')
    backup=root/'runtime'/'code-backups'/('visibility-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True,exist_ok=False)
    installed=[]
    with single_instance(Path(config['state_dir'])/'instance.lock'):
        for name in names:
            target=root/'code'/name
            if target.exists():shutil.copy2(target,backup/name)
            temporary=target.with_name(target.name+'.update')
            shutil.copy2(staging/name,temporary);os.replace(temporary,target)
            installed.append(name)
    after=hashlib.sha256(config_path.read_bytes()).hexdigest()
    result={'installed':installed,'backup':str(backup),'config_unchanged':before==after,
            'live_task_started':False,'orders_submitted_by_updater':0}
    atomic_json(backup/'receipt.json',result)
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
