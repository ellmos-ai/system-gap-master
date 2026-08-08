# Configuration-state showroom

Copy `examples/config-state.providers.example.json` to this directory as
`providers.json`, replace the placeholder paths and keys with an explicit
allowlist, and keep `DEVIATIONS.md` under review. The snapshot script never
walks a provider directory: it reads only the configured files and keys.

From the repository root, run this on each machine using its own slot name:

```text
python scripts/config_snapshot.py all \
  --state-dir /path/to/SYNC/_config-state \
  --config /path/to/SYNC/_config-state/providers.json \
  --slot YOUR-HOST
```

Generated snapshots and `CONFIG-STATE.md` are disposable derived state. Keep
the provider table and rationale, but do not put credentials or live database
files in the yard.
