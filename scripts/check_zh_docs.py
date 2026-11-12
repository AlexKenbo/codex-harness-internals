#!/usr/bin/env python3
"""Read-only checks for Chinese documentation coverage, local links and source IDs."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
modules = sorted(ROOT.glob('SRC-[0-9][0-9][0-9][0-9]'))
ids = set()
counts = dict.fromkeys(('rules', 'structural', 'use-case'), 0)
for module in modules:
    for lens in counts:
        data = json.loads((module / f'{lens}.json').read_text())
        counts[lens] += len(data['compositions'])
        ids.update(item['id'] for item in data['compositions'])
errors = []
for name in ('README.md', 'README.ru.md'):
    if '[简体中文](./README.zh-CN.md)' not in (ROOT / name).read_text():
        errors.append(f'Missing Chinese language link in {name}')
for module in modules:
    if not (ROOT / 'docs/zh-CN' / f'{module.name}.md').is_file():
        errors.append(f'Missing Chinese guide: {module.name}')
files = [ROOT / 'README.zh-CN.md', ROOT / 'HOW-TO-QUERY.zh-CN.md', *sorted((ROOT / 'docs/zh-CN').glob('*.md'))]
for path in files:
    if not path.is_file():
        errors.append(f'Missing {path.name}')
        continue
    text = path.read_text()
    if not re.search(r'[\u4e00-\u9fff]', text):
        errors.append(f'No Chinese prose: {path.relative_to(ROOT)}')
    if len(re.findall(r'^```', text, re.M)) % 2:
        errors.append(f'Unbalanced code fences: {path.relative_to(ROOT)}')
    for link in re.findall(r'\]\(([^)]+)\)', text):
        if re.match(r'^[a-z]+://|^#', link):
            continue
        target = link.split('#', 1)[0]
        if not (path.parent / target).exists():
            errors.append(f'Broken local link: {path.relative_to(ROOT)} -> {link}')
    for item_id in re.findall(r'SRC-\d{4}/(?:rule|entity|uc\.sub|uc\.user|uc\.summary)/[a-zA-Z0-9_-]+', text):
        if item_id not in ids:
            errors.append(f'Unknown source ID in {path.relative_to(ROOT)}: {item_id}')
assert len(modules) == 18, f'Expected 18 modules, got {len(modules)}'
if errors:
    raise SystemExit('\n'.join(errors))
print(f'PASS: {len(modules)} module guides; {len(files)} Chinese documents; local links, source IDs, UTF-8 and code fences checked.')
print(f'Source counts: {counts}; total={sum(counts.values())}.')
