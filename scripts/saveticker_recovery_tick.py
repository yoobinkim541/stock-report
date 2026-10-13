"""Run one bounded monitored collection/recovery step."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))


def main(argv=None):
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env')
    load_dotenv(ROOT/'.env.hermes-source')
    sys.path.insert(0,str(Path(os.getenv('HERMES_SOURCE_AGENT_ROOT','~/.hermes/hermes-agent')).expanduser()))
    from reports.saveticker_auth import login_saved_email
    from reports.saveticker_recovery import DEFAULT_STATE, tick
    from reports.saveticker_remote_access import ensure_remote_access
    from reports.saveticker_runtime import DEFAULT_ROOT, collect_private
    import notify
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,default=Path(os.getenv('SAVETICKER_RECOVERY_STATE_PATH',str(DEFAULT_STATE))))
    parser.add_argument('--root',type=Path,default=DEFAULT_ROOT)
    parser.add_argument('--url',action='append')
    parser.add_argument('--limit',type=int,choices=range(1,21),default=3)
    args=parser.parse_args(argv)
    result=tick(state_path=args.state,
        collect_fn=lambda:collect_private(root=args.root,limit=args.limit,article_urls=args.url),
        login_fn=login_saved_email,remote_fn=ensure_remote_access,send_fn=notify.send_telegram)
    print(json.dumps(result,ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
