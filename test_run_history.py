from pathlib import Path
import tempfile
import unittest

from run_history import describe_run,load_runs,make_row,item_reason
from runtime import atomic_json


class HistoryTests(unittest.TestCase):
    def test_legacy_wait_is_not_submission_success(self):
        record={'action':'cycle','status':'finished','started_at':'2026-09-09T12:00:01+08:00',
                'finished_at':'2026-09-09T12:00:02+08:00','result':{'phase':'waiting_afternoon','attempts':0,'items':{}}}
        row=make_row(Path('old.json'),record)
        self.assertIn('未查询',row['result'])
        self.assertEqual(row['counts'],'未查询 / 0笔提交')
        self.assertEqual(row['duration'],'1.00s')

    def test_new_lunch_records_actual_query_and_zero_submission(self):
        record={'action':'cycle','status':'finished','activity':{'outcome':'queried_lunch','queried':True,'candidate_count':1,'submitted':0},
                'result':{'phase':'waiting_afternoon','next_due':'2026-09-09T13:00:00+08:00','items':{
                    '920229.BJ':{'status':'SKIPPED_SCOPE','name':'测试'}}}}
        row=make_row(Path('new.json'),record)
        self.assertEqual(row['result'],'已查询，午间不提交')
        self.assertEqual(row['counts'],'返回 1 条 / 0笔提交')
        self.assertIn('北交所无权限，已跳过',row['detail'])

    def test_cached_completion_does_not_claim_new_query(self):
        record={'action':'cycle','status':'finished','activity':{'outcome':'already_complete','queried':False,'submitted':0},
                'result':{'phase':'complete','items':{}}}
        row=make_row(Path('cache.json'),record)
        self.assertIn('本轮跳过',row['result']);self.assertIn('未查询',row['counts'])

    def test_monitor_attention_and_query_failure_are_visible(self):
        self.assertIn('监控提示',describe_run({'action':'monitor','status':'finished','result':{'issues':['daily_work_incomplete']}}))
        self.assertIn('失败',describe_run({'action':'cycle','status':'finished','result':{'phase':'retryable_error'}}))
        self.assertIn('失败',describe_run({'action':'cycle','status':'failed','error_type':'TimeoutError'}))

    def test_history_reads_all_types_and_keeps_bad_receipt_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);day='2026-09-09';runs=root/'runs'/day
            atomic_json(runs/'a.json',{'action':'cycle','status':'finished','started_at':day+'T12:00:00+08:00','result':{'phase':'waiting_afternoon','attempts':0}})
            atomic_json(runs/'b.json',{'action':'notify','status':'finished','started_at':day+'T12:02:00+08:00','result':{'pending_notifications':0}})
            (runs/'bad.json').write_text('broken',encoding='utf-8')
            rows=load_runs(root,day)
            self.assertEqual(len(rows),3);self.assertEqual(rows[0]['action'],'通知补发')
            self.assertTrue(any('不可读' in row['result'] for row in rows))

if __name__=='__main__':unittest.main(verbosity=2)
