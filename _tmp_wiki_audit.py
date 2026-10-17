#!/usr/bin/env python3
"""Audit wiki pages for recommendations."""
from agent_console import wiki
from collections import Counter

pages = wiki.list_pages(status='all', surface='all', limit=10000)
stats = wiki.stats()

print("=== ALL PAGES ===")
for p in pages:
    pid = p.get('id','')
    kind = p.get('kind','')
    status = p.get('status','')
    title = str(p.get('title',''))[:80]
    print(f'{status:12s} {kind:16s} {pid:42s} {title}')

print()
print(f'Total pages: {len(pages)}')

stale = wiki.list_stale_pages(max_age_days=14)
print(f'Stale (14d+): {len(stale)}')
unused = wiki.list_unused_pages(days=30)
print(f'Unused (30d+): {len(unused)}')

kc = Counter(p.get('kind','') for p in pages)
sc = Counter(p.get('status','') for p in pages)
print(f'Kinds: {dict(kc)}')
print(f'Statuses: {dict(sc)}')
print(f'Stats: {stats}')