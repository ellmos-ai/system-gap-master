# Read-only Installer partner handoff

`yard-instance-manager installer-handoff` exports `system-gap.installer-export.v1`
to stdout. Its `handoff` and `sync_evidence` objects are the existing canonical
Installer partner inputs. No source, yard, target, approval or grant is written.

Required options: `--yard-root`, `--profile-path`, `--profile-sha256`,
`--observation-path`, `--observation-sha256`, `--target-host`, `--inventory-host`.
`--template-root` may select the canonical yard template. The profile must be the
native `ellmos.system.v1` BACH product. The first observation adapter accepts only
`codex.bach.deployment-observation.v1`, with a matching target host, immutable BACH
commit and a timestamp no more than 24 hours old (five minutes future tolerance).

The exporter verifies exact input bytes and identity. Runtime facts remain
attributed to the external observation; the exporter does not repeat runtime
probes or verify a signature. Local yard inventory reads top-level metadata and
the public template, never private yard file contents. Only its summary and hash
are exported. Inventory host and partner target host are separate, and a local
inventory does not verify a remote yard. Unexpected observation fields are not
copied. Profile/observation source pointers and SHA-256 pins remain reviewable.

Each object is hashed by the existing native canonical digest helper. These
hashes are integrity bindings, not authority. Separate owner approval, external
signed capability grant, actual source resolution and all normal Installer/Ocean
mutation checks remain required. This output does not claim an audit PASS,
decision-writer adoption or full runtime composition.
