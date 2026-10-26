"""Names for independent broker installations. Legacy names remain unchanged."""
from pathlib import Path
import re
from runtime import read_json

DEFAULT_PREFIX = 'QmtIPO3'
ACTIONS = ('Cycle', 'Notify', 'Monitor', 'Backup')


def display_label(config):
    label = config.get('instance_label', {'QmtIPO-Oriental':'东方证券', 'QmtIPO-Northeast':'东北证券'}.get(task_prefix(config), ''))
    if not isinstance(label, str) or len(label) > 16 or any(c in label for c in ('\r','\n','\x00')):
        raise ValueError('invalid_instance_label')
    return label


def task_prefix(config=None):
    prefix = (config or {}).get('task_prefix', DEFAULT_PREFIX)
    if not isinstance(prefix, str) or not re.fullmatch(r'QmtIPO[A-Za-z0-9-]{0,40}', prefix):
        raise ValueError('invalid_task_prefix')
    return prefix


def task_names(config=None):
    return tuple(task_prefix(config)+'-'+action for action in ACTIONS)


def root_config(root):
    path = Path(root)/'config.json'
    return read_json(path) if path.is_file() else {}


def root_task_names(root):
    return task_names(root_config(root))


def mutex_name(root=None):
    prefix = task_prefix(root_config(root)) if root is not None else DEFAULT_PREFIX
    return 'Local\\QmtIpoPanelControl'+('' if prefix == DEFAULT_PREFIX else '-'+prefix)


def initialize_prefix(root, prefix):
    """Only the unused NEW installer can bind a namespace; never rename live tasks."""
    import sqlite3
    from runtime import atomic_json
    config = root_config(root)
    candidate = task_prefix({'task_prefix': prefix})
    if config.get('enable_execution') is not False:
        raise ValueError('execution_must_be_disabled')
    control = Path(config['control_dir'])
    if (control/'panel-authorization.json').exists() or any((control/'daily').glob('*-live.json')):
        raise ValueError('used_installation_namespace_immutable')
    if config.get('task_prefix', candidate) != candidate:
        raise ValueError('namespace_mismatch')
    db = sqlite3.connect((Path(config['state_dir'])/'state.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)
    try:
        if any(db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('intents', 'outbox')):
            raise ValueError('used_installation_namespace_immutable')
    finally:
        db.close()
    if prefix != DEFAULT_PREFIX and 'task_prefix' not in config:
        atomic_json(Path(root)/'config.json', dict(config, task_prefix=candidate))
