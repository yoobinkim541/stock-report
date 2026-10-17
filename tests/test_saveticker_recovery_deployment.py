"""Recovery drains a bounded batch of current articles, not a fixed historical URL."""
import configparser
from pathlib import Path
import shlex


def test_recovery_service_discovers_ten_articles_per_bounded_tick():
    unit = configparser.ConfigParser(interpolation=None)
    unit.read(Path(__file__).resolve().parents[1] / 'deploy/stock-report-saveticker-recovery.service')
    command = shlex.split(unit['Service']['ExecStart'])
    assert command[-2:] == ['--limit', '10']
    assert '--url' not in command
    assert 'scripts/saveticker_recovery_tick.py' in command
    assert unit['Service']['MemoryMax'] == '2G'
    assert unit['Service']['MemorySwapMax'] == '0'
