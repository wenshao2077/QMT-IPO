"""Maintainer command: hash the install whitelist, excluding local secrets/state."""
import hashlib
import json
from pathlib import Path
from installer import destination
from release_info import BASE_COMMIT, VERSION


# Reviewed payload names. Unlisted local files never enter a delivery artifact.
SOURCE_FILES = (
    'AGENTS.md',
    'AI_DEPLOY.md',
    'COMPATIBILITY.json',
    'DEPLOY_PROTOCOL.json',
    'GITHUB_HANDOFF.md',
    'README.md',
    'TEST_RESULTS.md',
    'app.py',
    'broker.py',
    'build_manifest.py',
    'build_release.py',
    'build_distribution.py',
    'offline_dependencies.py',
    'dependencies.lock.json',
    'requirements-offline.txt',
    'delivery/setup.ps1',
    'delivery/verify.ps1',
    'delivery/start.ps1',
    'docs/FINAL_DELIVERY.md',
    'docs/ROUND3.md',
    'test_final_delivery.py',
    'calendar_health.py',
    'calendar_tool.py',
    'calendars/2026.json',
    'config.example.json',
    'config.schema.json',
    'configuration.py',
    'configure.py',
    'coordinator.py',
    'deploy.py',
    'deploy_visibility.py',
    'diagnostics.py',
    'discovery_policy.py',
    'docs/CALENDAR.md',
    'docs/DELIVERY_REVIEW.md',
    'docs/MAINTENANCE.md',
    'docs/ROUND1.md',
    'docs/ROUND1_TEST_RESULTS.md',
    'docs/ROUND2.md',
    'docs/ROUND2_TEST_RESULTS.md',
    'docs/RUNTIME_ADVISORIES.md',
    'docs/WINDOWS_ROUND1_ACCEPTANCE.md',
    'docs/WINDOWS_ROUND2_ACCEPTANCE.md',
    'docs/Windows安装与升级验收.md',
    'environment_check.py',
    'hidden.vbs',
    'install.ps1',
    'install_journal.py',
    'install_panel.ps1',
    'installer.py',
    'integration_permissions.py',
    'launcher.py',
    'maintenance.py',
    'market_calendar.py',
    'mini_base.py',
    'notification_policy.py',
    'package_verify.py',
    'panel.py',
    'panel_backend.py',
    'panel_tasks.ps1',
    'privacy.py',
    'probe_readonly.py',
    'release_info.py',
    'requirements.txt',
    'retention.py',
    'run_history.py',
    'run_scheduled.ps1',
    'runtime.py',
    'setup.ps1',
    'stage.ps1',
    'support.py',
    'task_identity.ps1',
    'tasks.ps1',
    'test_calendar_delivery.py',
    'test_delivery_round1.py',
    'test_discovery_round1.py',
    'test_maintenance_round2.py',
    'test_package_round2.py',
    'test_panel.py',
    'test_release_round1.py',
    'test_run_history.py',
    'test_system.py',
    'test_tasks.ps1',
    'test_ui_round2.py',
    '使用说明.html',
    '启用每日自动打新.ps1',
    '开始安装.cmd',
    '查看自动打新状态.ps1',
    '桌面控制台使用说明.md',
    '运行记录与午间查询说明.md',
    '需求与运行说明.md',
    '验收记录.md',
)

def build(root):
    root=Path(root).resolve()
    files={}
    paths=[root/name for name in SOURCE_FILES]
    for path in sorted(paths, key=lambda p:p.relative_to(root).as_posix()):
        if not path.is_file() or path.is_symlink():
            raise ValueError('Reviewed source file missing or linked: '+path.name)
        name=path.relative_to(root).as_posix()
        try:destination(name)
        except ValueError:continue
        files[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    # Source metadata must hash identically after Git's LF checkout on every OS.
    data={'schema_version':1,'version':VERSION,'base_commit':BASE_COMMIT,'files':files}
    (root/'DELIVERY_MANIFEST.json').write_bytes((json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    from build_release import source_contents
    contents = source_contents(root)
    for name in ('SOURCE_IDENTITY.json', 'COMPONENTS.json'):
        (root/name).write_bytes(contents[name])
    return files


if __name__=='__main__':build(Path(__file__).parent)
