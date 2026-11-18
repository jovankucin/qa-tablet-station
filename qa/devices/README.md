# qa/devices

Per-station records. Every `*.json` here is in `qa/.gitignore`: each station writes its own.

- **Tablet profiles:** `python3 -I qa/runner/measure_device.py <serial> <tag> <slot> --write` writes
  `<serial>.v<N>.json`. Keep the tags TA5, ET65 and TA3 (several files list exactly these tags). The CDP port is
  9341 + slot.
- **Account record:** copy `templates/accounts.example.json` to `accounts.v1.json` and fill it in. Its `label` must
  equal `account` in `qa/runner/engine.json`.

Do not put example files here: run.py, act.py and status.py load every `*.json` in this folder.
