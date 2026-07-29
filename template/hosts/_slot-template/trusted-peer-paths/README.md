# trusted-peer-paths/ — signed path registry for this host

Only this host publishes `registry.json` here. Generate it atomically with:

```text
trusted-peer-paths publish --config <HOST_LOCAL_CONFIG> --entries <HOST_LOCAL_ENTRIES>
```

The registry may contain exact local credential and ordinary-file paths,
path IDs, SFTP endpoints and peer allowlists. It never contains file content,
credential values, HMAC keys, SSH private keys or local verification state.
All keys/config/state remain outside the synced yard.

Peers verify the signature and revision before `list`, `resolve`,
`pull-plan`, or `pull --apply`. SQLite, `-wal` and `-shm` paths are discovery
only and must name `sqlite-transit-sync`; they travel through the R9
`db-transit/<namespace>` snapshot flow, never direct SFTP.
