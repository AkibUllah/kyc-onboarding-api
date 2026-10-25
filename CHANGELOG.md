# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- CodeQL code scanning workflow and Dependabot configuration (pip, Docker, GitHub Actions).
- API reference and terminal screenshots in the README.
- Roadmap tracked as GitHub issues under the `v0.2.0` milestone.

### Changed

- CI workflow runs with a read-only `GITHUB_TOKEN` by default.

### Fixed

- `scripts/demo_flow.py` waits for the background checks to finish after submitting, so it reports the real outcome (auto-approved / under review) instead of `submitted`.

## [0.1.0] - 2026-10-25

First public release.

### Added

- Applicant lifecycle (`draft → submitted → under_review → approved | rejected | needs_more_info`) enforced by a transition table, with every transition audited.
- Document intake for NID front/back and selfie with magic-byte sniffing, size limits, filename sanitising, SHA-256 hashing and replace-on-reupload.
- `Storage` protocol with local and S3-compatible backends.
- Pluggable checks: NID format, date-of-birth consistency, face match (protocol + stub), sanctions/PEP screening with fuzzy name matching, duplicate identity detection.
- Weighted risk scoring with configurable thresholds and optional auto-approval of low-risk applicants.
- Checks run as background tasks after submission; admins can re-run them synchronously.
- Reviewer queue, claim and decision endpoints; three API-key roles (`agent`, `reviewer`, `admin`).
- Daily and SLA statistics built from the audit trail.
- Error envelope, request ids, `/health`, Docker image, docker compose with PostgreSQL, CI on Python 3.10/3.11, Pages API reference, Codespaces and Render kits.

### Fixed

- `postgres://` and `postgresql://` database URLs are rewritten to the psycopg 3 driver.

[Unreleased]: https://github.com/AkibUllah/kyc-onboarding-api/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/AkibUllah/kyc-onboarding-api/releases/tag/v0.1.0
