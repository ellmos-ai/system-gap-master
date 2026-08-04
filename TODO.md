# Pre-Release TODO: system-gap-master

**Audit Date:** 2026-07-21
**Auditor:** Antigravity / Gemini githubbot-one-repo-cleaner
**Target Repo:** `ellmos-ai/system-gap-master`

---

## BLOCKER

- [x] **Secrets:** No API keys, tokens or passwords found in tracked files by Final Gate Check.
- [x] **Private Data:** No PII patterns found in tracked files by Final Gate Check.
- [x] **Hardcoded Paths:** No hardcoded personal paths found by Final Gate Check.
- [x] **Database Files:** No `.db` files are tracked; local database outputs are ignored.
- [x] **.env Files:** No `.env` files are tracked; local env files are ignored.
- [x] **BACH Internals:** No BACH-internal documents found in the release surface.
- [x] **.gitignore:** Minimum release-gate entries are present.
- [x] **LICENSE:** MIT license file is present.
- [x] **README.md:** English README is present and complete enough for the current public protocol release.

---

## HIGH PRIORITY

- [x] Add a user- and system-neutral trusted-peer direct-pull executor without
  weakening the read-only planner: detached registry/grant signatures,
  host-local credential binding, host-key pinning, one-shot replay ledger,
  bounded single-file SFTP read, no-replace commit and redacted local receipt.
- [x] Add a small regression test for `scripts/system_gap_daily_check.py` using a temporary yard.
- [ ] **Port the user-neutral OneDrive tree reconciler after the private
  two-system pilot.** Source candidate:
  `.SYNC/central-skills/onedrive-tree-reconciler/`. Preserve its paired
  metadata-scan contract, fail-closed `PARTIAL` handling, source-local
  availability gate, cloud-only no-hydration/no-transfer invariant,
  no-overwrite staging import, SHA-256 bundles, and bidirectional roles.
  Before adding it to this public repo, remove deployment-specific roots and
  transport assumptions behind configuration, keep all host/user names out of
  the release surface, add cross-platform or clearly Windows-scoped tests, and
  require the private pilot receipts as promotion evidence. *Noted
  2026-07-28 [C].*
- [ ] **Evaluate `fast-track-sync` as the urgent delivery layer.** The private
  pilot separates transport (verified SSH or Tailscale Taildrop), target-local
  lifecycle execution (COMA), and signed result return. Any public promotion
  must preserve HMAC authentication, TTL and nonce replay protection,
  receiver-side source/adapter/CWD/write allowlists, COMA dry-run before
  launch, and the explicit rule that Taildrop delivery alone is not remote
  execution. Keep key material and deployment-specific receiver policies out
  of the repository. *Noted 2026-07-28 [C].*
- [ ] Document one concrete setup example per agent family once the public repo wiring is stable.
- [ ] Decide whether the daily gate should optionally write JSON output for hook integrations.
- [x] **Finish the rename to `system-gap-master`.** User decision received
  2026-07-27. Repository metadata, documentation, the daily gate script and
  GitHub remote now use the new name. The old script name and
  `SYNC_MASTER_DIR` remain temporary compatibility aliases.
- [ ] **Port the config-state pattern from the private instance** (bidirectional-improvement rule,
  `~/CLAUDE.md` → "Verbesserungen beidseitig rückangleichen"). The private yard gained a
  **configuration showroom** on 2026-07-26: every machine drops an allowlist-filtered snapshot of
  how its AI agents are actually configured (Claude Code, Claude Desktop, Codex, Antigravity) into
  `_config-state/snapshots/<slot>.json`; a generated `CONFIG-STATE.md` diffs the machines and flags
  differences that lack a written rationale in the hand-maintained `DEVIATIONS.md`. It answers the
  question a sync yard otherwise leaves open: *machines drift in configuration, not just in files.*
  Reference implementation: `.SYNC/scripts/config_snapshot.py` (~330 LOC, zero dependencies,
  stdlib only — fits this repo's zero-dependency rule). For the public version, generalise the
  provider list (do not hardcode Anthropic/OpenAI/Google paths — make them a config table) and keep
  the two hard-won safety properties: **allowlist instead of blocklist**, and **`<HOME>` path
  normalisation** so Windows and macOS snapshots stay comparable.
  *Noted 2026-07-26 [C].*

---

## MEDIUM PRIORITY

- [ ] Add `SECURITY.md` before wider public promotion.
- [ ] Add `CONTRIBUTING.md` if external contributions become expected.
- [x] Add a GitHub Actions smoke workflow for the zero-dependency gate script.

---

## LOW PRIORITY

- [ ] Add badges only after CI and repository visibility are final.
- [ ] Add provider-specific conflict-copy examples for OneDrive, Dropbox and Syncthing.

---

## STATUS

| Category | Status | Notes |
|----------|--------|-------|
| Secrets | :green_circle: | Final Gate Check found no secret patterns. |
| Private Data (PII) | :green_circle: | Final Gate Check found no PII patterns. |
| .gitignore | :green_circle: | Minimum gate entries are present. |
| Language (English) | :green_circle: | Public README and protocol surface are English-first; German README note uses real umlauts. |
| BACH Internals | :green_circle: | No BACH-internal release documents found. |
| Database Files | :green_circle: | No tracked `.db` files. |
| README.md | :green_circle: | Present and English-first. |
| LICENSE | :green_circle: | MIT license present. |
| **Overall** | **READY** | Final Gate Check is green for the current release surface. |

**Audit Date:** 2026-07-15
**Gate Check Exit Code:** `0`

---

*Template version: 1.0 | Source: MODULES/_templates/TODO_TEMPLATE.md*
