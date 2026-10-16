"""Recovery must probe one current article, not an expiring historical URL."""
import configparser
from pathlib import Path
import shlex


def test_recovery_service_discovers_exactly_one_latest_article():
    unit = configparser.ConfigParser(interpolation=None)
    unit.read(Path(__file__).resolve().parents[1] / 'deploy/stock-report-saveticker-recovery.service')
    command = shlex.split(unit['Service']['ExecStart'])
    assert command[-2:] == ['--limit', '1']
    assert '--url' not in command
    assert 'scripts/saveticker_recovery_tick.py' in command
