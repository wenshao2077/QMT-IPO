"""Offline metadata checks. Never imports xtquant, connects, or sends a message."""
import importlib.metadata as metadata
import importlib.util
import json
import platform
import struct
import sqlite3
import sys

from release_info import PINNED_DEPENDENCIES, VERIFIED_PYTHON, VERSION



def sqlite_runtime_report(version=None):
    """Advisory only. No DLL/SDK update or journal-mode switch is performed.

    SQLite's official WAL docs identify fixed branches; vendor backports cannot be
    inferred from an older version string and must receive separate local review.
    """
    version = sqlite3.sqlite_version if version is None else version
    try:
        parts = tuple(int(x) for x in version.split('.'))
        if len(parts) != 3 or any(x < 0 for x in parts):
            raise ValueError('invalid_version')
        fixed = parts >= (3,51,3) or (parts[:2] == (3,44) and parts[2] >= 6) or (parts[:2] == (3,50) and parts[2] >= 7)
    except (AttributeError, TypeError, ValueError):
        version, fixed = None, False
    return {'version': version, 'wal_reset_fix_identified': fixed,
            'review_required': not fixed, 'automatically_updated': False,
            'advisory_source': 'https://sqlite.org/wal.html#walreset',
            'assessment_scope': 'upstream_version_string_not_vendor_backport_audit'}

def environment_report(*, system=None, python_version=None, bits=None, machine=None,
                       version_reader=None, module_finder=None):
    """Inspect metadata only; binary/client compatibility requires separate acceptance."""
    system = sys.platform if system is None else system
    python_version = tuple(sys.version_info[:3]) if python_version is None else tuple(python_version)
    bits = struct.calcsize('P') * 8 if bits is None else bits
    machine = platform.machine() if machine is None else machine
    version_reader = metadata.version if version_reader is None else version_reader
    module_finder = importlib.util.find_spec if module_finder is None else module_finder
    issues, packages = [], {}
    if system != 'win32':
        issues.append('windows_required')
    if python_version[:2] != VERIFIED_PYTHON:
        issues.append('use_verified_python_3_11')
    if bits != 64 or machine.lower() not in ('amd64', 'x86_64'):
        issues.append('python_x64_required')
    for name, required in PINNED_DEPENDENCIES.items():
        try:
            installed = version_reader(name)
        except metadata.PackageNotFoundError:
            installed = None
            issues.append('dependency_missing:' + name)
        except Exception:
            installed = None
            issues.append('dependency_metadata_unreadable:' + name)
        else:
            if installed != required:
                issues.append('dependency_version_mismatch:' + name)
        packages[name] = {'required': required, 'installed': installed}
    try:
        tkinter_present = module_finder('tkinter') is not None and module_finder('_tkinter') is not None
    except (ImportError, ValueError, AttributeError):
        tkinter_present = False
    if not tkinter_present:
        issues.append('tkinter_missing')
    return {'schema_version': 1, 'version': VERSION, 'ok': not issues,
            'level': 'local_environment_only', 'issues': issues,
            'system': system, 'python': list(python_version), 'bits': bits,
            'machine': machine, 'dependencies': packages,
            'tkinter_module_present': tkinter_present,
            'sqlite_runtime': sqlite_runtime_report(),
            'unverified': ['sdk_client_binary_compatibility', 'visible_desktop',
                           'account_permissions', 'broker_acceptance'],
            'account_connected': False, 'message_sent': False, 'submission_calls': 0}


def main():
    result = environment_report()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
