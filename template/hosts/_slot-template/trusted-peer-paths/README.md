# trusted-peer-paths/ — host-owned path metadata

Only this host's separately reviewed publisher may place `registry.json`
here. The `trusted-peer-paths` CLI in this repository is read-only and never
writes this slot.

The registry may contain exact approved SFTP paths, path IDs, read-only
endpoints, network labels, known-host pins and peer allowlists. Every path
declares `metadata_type=path-location` and `content_included=false`. File
content, credential values, private keys, tokens and passwords are forbidden.

Peers may run `validate`, `list`, `resolve` and `pull-plan`. The result is a
non-executable preparation receipt: no network contact, credential read or
file transfer occurs. Detached-signature verification, real SSH setup and a
reviewed transfer executor remain activation gates. SQLite, `-wal` and
`-shm` paths are discovery-only and use the R9
`sqlite-transit-sync`/`db-transit/<namespace>` flow.
