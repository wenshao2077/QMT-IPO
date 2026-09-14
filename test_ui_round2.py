"""Round-2 desktop interaction tests; all controllers and notifications are fakes."""
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from panel import Panel
from panel_backend import Backend
from test_panel import snapshot
from runtime import atomic_json
from support import Store


class MaintenanceUI(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw();self.backend=Mock();self.confirm=Mock(return_value=False)
        self.ui=Panel(self.root,self.backend,autorefresh=False,confirm=self.confirm)
        self.ui.async_job=Mock()
        self.net=patch('socket.socket',side_effect=AssertionError('Network forbidden'));self.net.start()
    def tearDown(self):
        self.net.stop();self.ui.stop();self.root.update_idletasks();self.root.destroy()
    def test_open_does_not_export_or_apply_recovery(self):
        self.backend.export_support.assert_not_called();self.backend.recovery_plan.assert_not_called()
    def test_cancel_export_never_calls_backend(self):
        self.ui.export_support();self.ui.async_job.assert_not_called()
    def test_cancel_file_selection_does_not_export(self):
        self.confirm.return_value=True
        with patch('panel.filedialog.asksaveasfilename',return_value=''):self.ui.export_support()
        self.ui.async_job.assert_not_called()
    def test_confirm_export_passes_explicit_consent_once(self):
        self.confirm.return_value=True
        with patch('panel.filedialog.asksaveasfilename',return_value='FAKE-OUTPUT.zip'):
            self.ui.export_support();self.ui.export_support()
        self.ui.async_job.assert_called_once()
        self.ui.async_job.call_args.args[1]()
        self.backend.export_support.assert_called_once_with('FAKE-OUTPUT.zip',confirmed=True)
        self.backend.change.assert_not_called()
    def test_recovery_available_even_when_task_snapshot_failed(self):
        self.ui.snapshot=None;self.ui.update_buttons();self.ui.recovery_plan()
        self.ui.async_job.call_args.args[1]()
        self.backend.recovery_plan.assert_called_once();self.backend.change.assert_not_called()
    def test_notification_unknown_not_zero_or_delivered(self):
        row=snapshot();row.update(pending_notifications=None,failed_notifications=None,notifications_readable=False)
        self.ui.render(row)
        self.assertIn('未知',self.ui.cards['notify']['text'])
        self.assertNotIn('无待发',self.ui.cards['notify']['text'])
    def test_old_delivery_time_is_explicit_unknown(self):
        row=snapshot();row['last_notification_confirmed_at']=None
        self.ui.render(row)
        self.assertIn('不补造',self.ui.maintenance.get('1.0','end'))
    def test_calendar_expiry_exposes_maintenance_action(self):
        row=snapshot(True);row['calendar_coverage']={'status':'expiring','verified_through':'2026-12-31','first_unknown_day':'2027-01-01','issue_code':'calendar_coverage_expiring'}
        self.ui.render(row)
        self.assertIn('维护事项',self.ui.subtitle['text'])
        self.assertIn('2027-01-01',self.ui.maintenance.get('1.0','end'))
        self.backend.change.assert_not_called()
    def test_health_warning_does_not_say_all_ok(self):
        row=snapshot(False);row['local_health']={'status':'attention','issues':['ledger_unavailable']}
        self.ui.render(row)
        self.assertIn('账本不可读',self.ui.maintenance.get('1.0','end'))
    def test_recovery_result_shown_without_mutation(self):
        self.ui.events.put(('recovery',{'ok':True,'actions':[{'instruction':'FAKE READ ONLY PLAN'}]},None))
        self.ui.poll()
        self.assertIn('FAKE READ ONLY PLAN',self.ui.maintenance.get('1.0','end'))
        self.backend.change.assert_not_called()


if __name__=='__main__':unittest.main(verbosity=2)
