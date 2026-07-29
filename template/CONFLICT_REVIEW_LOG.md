# Conflict-copy review log — one row per host per day

> Gate for the daily conflict-copy sweep (PROTOCOL rule R7). File-sync
> providers create conflict copies on concurrent edits. Discover them with
> the configured reconciler, but never infer canonicality from a filename.
> Log a path-redacted receipt only after lease, backup, apply and verify are
> green. Blocked candidates remain untouched.

| date | host | ready | blocked | operation/receipt status |
|---|---|---|---|---|
