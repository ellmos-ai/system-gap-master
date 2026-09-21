# Third-Party Licenses & Transparency Notice

> **Project:** `ellmos-ai/system-gap-master`
> **Audited:** 2026-09-21
> **Repository License:** [MIT License](LICENSE)
> **Canonical Notice:** [NOTICE](NOTICE)
> **Architecture & Privacy:** 100% Local-First, Zero-Egress, Unprivileged User-Mode (`RunAsInvoker`), Fail-Closed

---

## Executive Summary & Compliance Assurance

`system-gap-master` is engineered under strict architectural and governance invariants: **100% Local-First, Zero-Egress by default, unprivileged user-mode execution (`RunAsInvoker`), and fail-closed state reconciliation**. All host slot management, message ingestion/deletion, instance template maintenance, configuration snapshots, and conflict-copy reconciliations operate entirely within local process and local filesystem boundaries.

All direct, optional, and development dependencies utilized across `system-gap-master` are distributed under strictly **permissive and free open-source licenses** (MIT, Apache-2.0, PSFL, LGPL-2.1). There are **zero AGPL or restrictive copyleft constraints**, ensuring maximum portability for multi-machine setups, personal transfer yards, enterprise infrastructure, and automated multi-agent deployments.

Furthermore, `system-gap-master` guarantees:
1. **100% Local-First & Zero Egress (INV-LOCAL-01):** Operates entirely on local filesystems and local storage mounts (OneDrive, Dropbox, Syncthing, NAS, or local paths). Zero network telemetry, zero phone-home calls, and zero hidden analytical tracking.
2. **Unprivileged User-Mode (`RunAsInvoker` / INV-SEC-02):** Executes safely in unprivileged user space without requiring root or administrator elevation.
3. **Strict Machine-Owned Slot Isolation (INV-SLOT-03):** Each host writes exclusively to its own designated slot (`hosts/<hostname>/`); peer slots are strictly read-only, preventing multi-machine collision by design.
4. **Delete-After-Read Direct Messaging (INV-MSG-04):** Inter-machine messages (`messages/to-<host>.md`) are processed and atomically removed to enforce at-most-once processing semantics.
5. **Fail-Closed Locking & Lease Enforcement (INV-FAIL-05):** Multi-agent and reconciler operations require valid exclusive kernel-backed leases (`reconciler.lock`); operations abort safely upon lock collision or lease expiry.
6. **Deterministic 3-Way & Append-Only Reconciliation (INV-MERGE-06):** Provider conflict copies are reconciled via deterministic base-merge or timestamped append; destructive overwrites are strictly prohibited.
7. **Gated Preflight & Idempotent Daily Ritual (INV-GATE-07):** The daily sync gate (`scripts/system_gap_daily_check.py`) enforces once-per-day execution with audit trail in `DAILY_SYNC_LOG.md`.
8. **Cryptographically Bound SFTP & Detached Verification (INV-PEER-08):** Trusted-peer preparation enforces sha256 checksums and detached Ed25519/GPG signatures; unsigned or tampered payloads are rejected fail-closed.
9. **100% Permissive Audited Dependency Stack (INV-LIC-09):** Clean MIT/PSFL stack audited in this document, zero copyleft or AGPL contamination.
10. **Dual Security Response & Triage SLA (INV-SLA-10):** Commitments to 48-hour response and 5-day triage via canonical security channels (`security@open-bricks.org`, `security@ellmos.ai`).

---

## Runtime Dependency Matrix

| Package | Role / Functional Scope | License | Project Repository / Upstream |
|:---|:---|:---|:---|
| **Python Standard Library** | Core CLI runner, file arithmetic, process execution, hashlib, json, logging, path manipulation | [PSFL-2.0](https://docs.python.org/3/license.html) | [python/cpython](https://github.com/python/cpython) |
| **tomli** (Python < 3.11) | Standard TOML parsing fallback for Python 3.10 runtime environments | [MIT](https://github.com/hukkin/tomli/blob/master/LICENSE) | [hukkin/tomli](https://github.com/hukkin/tomli) |

---

## Optional Execution Adapter Dependencies

| Package | Role / Functional Scope | License | Project Repository / Upstream |
|:---|:---|:---|:---|
| **paramiko** (optional `trusted-peer-sftp`) | SSH2 / SFTP protocol implementation for authorized, signature-verified trusted-peer transfers | [LGPL-2.1](https://github.com/paramiko/paramiko/blob/main/LICENSE) | [paramiko/paramiko](https://github.com/paramiko/paramiko) |
| **ticket-master** (optional `ticket-routing`) | Task ticket management and route intent adapter contract validation | [MIT](https://github.com/dev-bricks/ticket-master/blob/main/LICENSE) | [dev-bricks/ticket-master](https://github.com/dev-bricks/ticket-master) |

---

## Development & Quality Assurance Tooling

| Package | Usage & Purpose | License | Source / Upstream |
|:---|:---|:---|:---|
| **pytest** | Automated test runner, contract verification suites, mock fixtures | [MIT](https://github.com/pytest-dev/pytest/blob/main/LICENSE) | [pytest-dev/pytest](https://github.com/pytest-dev/pytest) |
| **ruff** | High-performance Python linter and code formatting enforcement | [MIT / Apache-2.0](https://github.com/astral-sh/ruff/blob/main/LICENSE-MIT) | [astral-sh/ruff](https://github.com/astral-sh/ruff) |
| **setuptools** | Standard package build backend (PEP 517 / PEP 621 compliant) | [MIT](https://github.com/pypa/setuptools/blob/main/LICENSE) | [pypa/setuptools](https://github.com/pypa/setuptools) |

---

## Full License Texts (Excerpts & Notices)

### 1. Python Software Foundation License Version 2 (PSFL-2.0)
Python standard library modules are used under the PSF License Agreement.  
Copyright (c) 2001-2026 Python Software Foundation. All rights reserved.

### 2. MIT License (MIT)
Used by `system-gap-master`, `tomli`, `ticket-master`, `pytest`, and `setuptools`.

> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:  
>  
> The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.  
>  
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

### 3. Apache License Version 2.0 (Apache-2.0)
Co-licensed by `ruff`.

> Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the License. You may obtain a copy of the License at:  
> http://www.apache.org/licenses/LICENSE-2.0  
> Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.

### 4. GNU Lesser General Public License Version 2.1 (LGPL-2.1)
Used optionally by `paramiko` under the `trusted-peer-sftp` extra.

> This library is free software; you can redistribute it and/or modify it under the terms of the GNU Lesser General Public License as published by the Free Software Foundation; either version 2.1 of the License, or (at your option) any later version.  
> This library is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
