"""GUI/control tests use fake controllers only; never enable real tasks or orders."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import tkinter as tk

from panel import Panel
from panel_backend import Backend,TASK_NAMES,WindowsController,plan_state


def snapshot(enabled=False):
    return {'ok':True,'execution_enabled':enabled,'needs_admin':False,'account_tail':'TEST',
            'markets':['SH','SZ'],'qmt_path':'模拟QMT目录','qmt_process_present':True,'quote_process_present':True,
            'webhook_configured':True,'pending_notifications':0,'history':[],
            'tasks':[{'name':n,'exists':True,'enabled':enabled,'next_run':'2026-09-10T09:35:00+08:00'} for n in TASK_NAMES],
            'daily':None}


class StateTests(unittest.TestCase):
    def test_enabled_needs_config_and_all_tasks(self):
        self.assertEqual(plan_state(snapshot(True))[0],'enabled')
        row=snapshot(True);row['tasks'][0]['enabled']=False
        self.assertEqual(plan_state(row)[0],'inconsistent')
        row=snapshot(False);row['tasks'][0]['enabled']=True
        self.assertEqual(plan_state(row)[0],'inconsistent')
    def test_unknown_not_silent_disabled(self):
        self.assertEqual(plan_state({'ok':False})[0],'unknown')
        row=snapshot(False);row['tasks'][0]['exists']=False
        self.assertEqual(plan_state(row)[0],'inconsistent')
    def test_pause_allows_notification_helpers_to_remain(self):
        row=snapshot(True);row['execution_enabled']=False;row['tasks'][0]['enabled']=False
        self.assertEqual(plan_state(row)[0],'disabled')
    def test_no_confirmation_no_mutation(self):
        controller=Mock();backend=Backend(Path.cwd(),controller)
        with self.assertRaises(ValueError):backend.change('Enable')
        controller.call.assert_not_called()
    def test_permission_preparation_precedes_enable(self):
        before=snapshot();before['needs_admin']=True
        controller=Mock();controller.call.side_effect=[before,snapshot(),{'ok':True}]
        controller.elevate_prepare.return_value={'ok':True}
        result=Backend(Path.cwd(),controller).change('Enable',confirmed=True)
        self.assertTrue(result['ok']);controller.elevate_prepare.assert_called_once()
        self.assertEqual(controller.call.call_args_list[-1].args,('Enable',))
    def test_cancel_windows_consent_does_not_enable(self):
        controller=Mock();before=snapshot();before['needs_admin']=True
        controller.call.return_value=before;controller.elevate_prepare.return_value={'ok':False,'error':'cancelled'}
        self.assertFalse(Backend(Path.cwd(),controller).change('Enable',confirmed=True)['ok'])
        controller.call.assert_called_once_with('Snapshot')
    def test_existing_permission_no_uac(self):
        controller=Mock();controller.call.side_effect=[snapshot(),{'ok':True}]
        Backend(Path.cwd(),controller).change('Enable',confirmed=True)
        controller.elevate_prepare.assert_not_called()
    def test_command_whitelist_and_confirmation(self):
        control=WindowsController(Path.cwd())
        with self.assertRaises(ValueError):control.command('Other')
        with self.assertRaises(ValueError):control.command('Enable')
        cmd=control.command('Snapshot')
        self.assertIn('-EncodedCommand',cmd)
        self.assertNotIn('--live',str(cmd))


class UiTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw()
        self.backend=Mock();self.confirm=Mock(return_value=False)
        self.ui=Panel(self.root,self.backend,autorefresh=False,confirm=self.confirm)
        self.ui.async_job=Mock()
    def tearDown(self):
        self.ui.stop();self.root.update_idletasks();self.root.destroy()
    def test_opening_ui_never_enables(self):
        self.backend.change.assert_not_called()
        self.assertEqual(str(self.ui.buttons['enable']['state']),'disabled')
    def test_cancel_button_dialog_never_dispatches(self):
        self.ui.render(snapshot());self.ui.enable()
        self.confirm.assert_called_once();self.ui.async_job.assert_not_called()
    def test_confirm_dispatches_once_and_locks_buttons(self):
        self.confirm.return_value=True
        self.ui.render(snapshot());self.ui.enable();self.ui.enable()
        self.ui.async_job.assert_called_once()
        self.assertEqual(str(self.ui.buttons['enable']['state']),'disabled')
        callback=self.ui.async_job.call_args.args[1];callback()
        self.backend.change.assert_called_once_with('Enable',confirmed=True)
    def test_enabled_shows_pause_not_double_enable(self):
        self.ui.render(snapshot(True))
        self.assertEqual(str(self.ui.buttons['enable']['state']),'disabled')
        self.assertEqual(str(self.ui.buttons['pause']['state']),'normal')
    def test_pause_confirmation(self):
        self.confirm.return_value=True;self.ui.render(snapshot(True));self.ui.pause()
        self.ui.async_job.call_args.args[1]()
        self.backend.change.assert_called_once_with('Pause',confirmed=True)
    def test_daily_empty_not_claim_no_ipo(self):
        self.ui.render(snapshot())
        self.assertEqual(self.ui.cards['today']['text'],'尚未开始')
    def test_actual_daily_items_render(self):
        row=snapshot(True);row['daily']={'day':'2026-09-09','phase':'complete','items':{
            '754810.SH':{'name':'模拟新债','kind':'BOND','quantity':10000,'status':'REPORTED'}}}
        self.ui.render(row)
        self.assertEqual(len(self.ui.table.get_children()),1)
        values=self.ui.table.item(self.ui.table.get_children()[0],'values')
        self.assertIn('10000 张',values);self.assertIn('券商已报',values)
    def test_connection_is_readonly_dispatch(self):
        self.ui.render(snapshot());self.ui.check()
        self.ui.async_job.call_args.args[1]()
        self.backend.check_connection.assert_called_once();self.backend.change.assert_not_called()
    def test_suppressed_notifications_are_not_presented_as_delivered(self):
        row=snapshot();row['suppressed_notifications']=3
        self.ui.render(row)
        self.assertIn('已抑制',self.ui.cards['notify']['text'])
        self.assertNotIn('已送达',self.ui.cards['notify']['text'])
    def test_validation_never_dispatches_notification_send(self):
        self.ui.render(snapshot());self.ui.validate_configuration()
        self.ui.async_job.call_args.args[1]()
        self.backend.validate_configuration.assert_called_once()
        self.backend.test_notification.assert_not_called()
        self.backend.change.assert_not_called()
    def test_notification_test_cancel_never_sends(self):
        self.ui.render(snapshot());self.ui.test_notification()
        self.confirm.assert_called_once()
        self.ui.async_job.assert_not_called()
        self.backend.test_notification.assert_not_called()
    def test_notification_test_requires_explicit_confirmed_send(self):
        self.confirm.return_value=True
        self.ui.render(snapshot());self.ui.test_notification()
        self.ui.async_job.call_args.args[1]()
        self.backend.test_notification.assert_called_once_with(confirmed=True)
        self.backend.change.assert_not_called()
    def test_layout_has_no_hidden_primary_action(self):
        self.root.deiconify();self.root.update()
        if not self.root.winfo_viewable():self.skipTest('SSH service session cannot map a visible desktop; live panel smoke checks layout')
        for name in ('enable','pause','check','logs'):
            widget=self.ui.buttons[name]
            self.assertTrue(widget.winfo_ismapped())
            # Font metrics differ across runners; require the whole requested
            # button (text + padding), not a fixed Windows pixel threshold.
            self.assertGreaterEqual(widget.winfo_width(),widget.winfo_reqwidth())
            self.assertLess(widget.winfo_rootx()+widget.winfo_width(),self.root.winfo_rootx()+self.root.winfo_width()+1)
        self.assertGreater(self.ui.run_table.winfo_height(),120)


if __name__=='__main__':unittest.main(verbosity=2)
