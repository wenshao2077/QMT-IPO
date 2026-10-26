"""Explicit XtQuant IPC binding; never infer the endpoint from the UI name."""
from pathlib import Path


def validate_userdata(value):
    path = Path(value)
    if not path.is_absolute() or not path.is_dir() or path.name.lower() not in ('userdata_mini', 'userdata'):
        raise ValueError('请核对已登录客户端的绝对 userdata_mini 或 userdata 路径')
    return path


def binding_report(value):
    path = validate_userdata(value)
    return {'userdata_kind': path.name.lower(),
            'up_queue_present': (path/'up_queue_xtquant').exists(),
            'selection': 'explicit_configuration', 'automatic_fallback': False}


class BrokerDiagnosticError(RuntimeError):
    """Only non-sensitive stage/field metadata may cross output boundaries."""
    def __init__(self, code, stage, *, return_code=None, row_index=None, field=None):
        self.code, self.stage = code, stage
        self.diagnostic = {'code': code, 'stage': stage}
        for key, value in (('return_code', return_code), ('row_index', row_index), ('field', field)):
            if value is not None:
                self.diagnostic[key] = value
        super().__init__(code)
