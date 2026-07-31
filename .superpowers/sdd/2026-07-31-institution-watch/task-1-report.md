status: completed
commits:
  - bfe889320a9eea185596f09f05cf77a3780a5506
  - 6cc1d83f75717fd0b1d190710076d4b1c62e85d2
test_summary: ".venv/bin/pytest tests/test_institution_watch.py -q -> 7 passed"
concerns:
  - "reports/institution_watch.py is matched by .gitignore (reports/*), so it must be staged with git add -f."
  - "Seed-backed snapshots still render in the comparison model, but their wiki digests now remain draft/unverified until real provenance is wired."
