# LLM Wiki Generation Gap Note

## What was happening

- `reports/source_wiki_curator.py` was already producing source-backed wiki pages, but the cron still used `--limit 8`, so higher-volume topics could crowd out lower-volume groups.
- `reports/wiki_distillation.py` only ran weekly, so source digests were not being promoted into reusable playbook/risk/concept cards often enough.
- Source digest bodies were short because each page only listed 8 events and did not include a richer summary of source distribution, repeated tickers, or follow-up questions.

## What we changed

- Removed the save cap in the production cron by switching the curator to `--limit 0`.
- Increased source digest body depth to include:
  - group-level summary,
  - source distribution,
  - repeated tickers,
  - signal type mix,
  - follow-up questions.
- Increased distillation cadence to every 6 hours so the wiki can refresh at least four times per day.

## Operational intent

- The curator should keep turning raw source events into source-backed digest pages frequently.
- The distiller should keep turning those digests into reusable judgment cards throughout the day.
- The health check should continue to flag stale sources, unlinked digests, and weak wiki hygiene so the system keeps accumulating durable knowledge instead of only preserving short summaries.

