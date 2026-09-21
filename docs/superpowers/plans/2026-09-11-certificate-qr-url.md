# Certificate QR URL update plan

**Goal:** Keep issued PNGs and update only their verification QR when PUBLIC_BASE_URL changes.
**Spec:** The design accepted in this task: preserve certificate code, text, signatures and all pixels outside QR squares; stage and verify before replacing; retain originals on failure; support legacy images without relying on today's template.
**Architecture:** Embed QR coordinates/URL in a PNG text chunk. A pure image service verifies/detects the old QR, replaces its square, decodes the new result and atomically publishes. A single background maintenance pass at web startup scans certificate records in batches and uses a PostgreSQL advisory lock. A CLI supports read-only preflight and explicit apply. No schema migration.
**Stack:** Existing Pillow/qrcode, zxing-cpp for independent decoding, FastAPI lifespan and PostgreSQL 18.

## Constraints

- Work directly in the existing D: checkout; preserve earlier dirty changes. No commits, resets, branch changes, pushes or new databases.
- Do not expose recipient data, secrets, full environment or certificate codes in logs.
- Keep PNG storage and current public verification behavior. Codes never change.
- Existing downloaded/printed copies cannot be updated remotely.
- Use the configured URL; do not invent a replacement hostname. A URL question is pending.
- Test with synthetic data and temporary files; production preflight reads only, then authorized file updates after validation.

## Task 1: Safe image update

Files: services/certificate_qr.py, services/image_builder.py, requirements.txt, tests/test_certificate_qr_update.py.

- [x] Add unittest checks for URL change, unchanged URL, pixel preservation, repeated changes, legacy detection, unrelated/multiple QR preservation, wrong-code images, invalid URL, bad metadata, failed publication and cleanup, and concurrent source edits. Observed missing-update assertion fail before implementation.
- [x] Implement `verification_url(base_url, code)`, `issuance_png_info(image, code)` and `update_certificate_qr(path, code, base_url, dry_run=False)`.
- [x] Metadata lives inside PNG, including square coordinates and decoded URL; discovery never uses current template positions.
- [x] For legacy images, locate decoded QR and verify its exact pixels against the existing generator before accepting a replacement square.
- [x] Stage PNG beside destination; independently decode target QR and compare outside pixels before os.replace. Failure leaves original bytes intact.
- [x] Embed metadata when issuing new certificates and preserve existing renderer behavior.
- [x] Run image tests with temporary directories, then renderer regressions in a network-disabled disposable app container.

## Task 2: Startup maintenance and operations

Files: services/qr_maintenance.py, main.py, scripts/update_certificate_qr.py, Dockerfile, README.md, tests/test_qr_maintenance.py.

- [x] Test batched scan, cancellation, one failed file not stopping later files, counters, singleton lock behavior, retry after busy startup, timeout reporting and CLI defaults using an external database boundary double only.
- [x] Query ID/code/image path only, by keyset with a fixed high-water ID; resolve managed paths before opening images.
- [x] Hold a PostgreSQL advisory lock across the pass; use a worker thread and a stop event; report aggregate counts plus internal numeric IDs/reason codes for failures.
- [x] Start after existing initialization; stop/join during lifespan shutdown. CLI defaults to dry-run; --apply updates files. Dedicated NullPool connections retain search_path=public and bound connection/query/lock/TCP waits; busy startup retries interruptibly.
- [x] Document recreate-web requirement after changing environment, preflight/apply commands, and old downloaded-copy limitation.
- [x] Review diff, run focused regressions and diff checks; build and inspect only web, preflight actual images before enabling maintenance; verify post-deploy health and aggregate QR results.

## Evidence

- 2026-09-12: Final candidate image passed 29 unittest checks in a disposable --network none container: 12 image-update, 1 issuance integration, 9 maintenance/CLI, 7 existing field-validation tests. No database was created or contacted by these tests.
- Read-only simulated URL replacement passed for all 109 workspace PNGs. These are historical workspace copies, distinct from live Docker storage.
- Live read-only preflight found 3 issued images, all already matching the configured Base URL. All 3 passed simulated replacement to an example.test URL without writing. Actual connection checks returned search_path=public, statement_timeout=5s, lock_timeout=3s.
- Independent review verified core image behavior; cleanup error and shallow metadata list issues were fixed/re-reviewed. Integration review requested busy-startup retry and bounded DB waits; both fixed and behavior-tested. Public schema parity was verified before deployment.
- User resumed on 2026-09-12 with containers stopped. Started the existing stack; initial backup failed while DB was stopped, then succeeded after startup: auto-20260912T074715Z-53f15eb0, integrity passed.
- Deployed only web with --no-deps --no-build --force-recreate --wait. Retained rollback image tag pkru-certificate:before-qr-20260912t074715. Candidate tag: pkru-certificate:qr-update-20260911.
- Post-deploy: web healthy; 3 page checks, 3 verification requests, 6 PNG responses (view/download), 3 JPEG downloads and 3 PDF downloads passed. All 3 live images contain QR metadata, and all image pixel hashes/modes/dimensions match pre-deploy. Maintenance dry-run reports unchanged=3, failed=0.
- PUBLIC_BASE_URL remains https://age-sky-scene-appliances.trycloudflare.com/ because no new URL was supplied. No external-host availability claim. Future URL changes require recreating the web container as documented.
- Earlier dirty changes were preserved; no Git commit/push/branch change or schema migration.
