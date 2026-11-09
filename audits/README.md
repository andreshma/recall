# Audits

Dated, advisory 360° audit reports for this repository live here. They are
point-in-time snapshots (security, reliability, data quality, privacy,
testing, maintainability, docs, supply chain) and never block a release on
their own — they inform it. Each report cites files and tests rather than
commit ids; findings are recorded as found, with their status and the test
that guards each fix.

| Date | Report | RAG | Summary | SBOM |
|---|---|---|---|---|
| 2026-11-09 | [report-recall-2026-11-09.md](report-recall-2026-11-09.md) | RED as found (1 High, publication) → GREEN after fixes, with accepted residuals | [JSON](summary/recall-2026-11-09.json) | CycloneDX: [runtime](sbom/recall-2026-11-09.cdx.json) · [dev/CI tools](sbom/recall-dev-2026-11-09.cdx.json) |
| 2026-11-06 | [report-recall-2026-11-06.md](report-recall-2026-11-06.md) | AMBER (0 Critical, 0 High, 6 Medium — all fixed) | [JSON](summary/recall-2026-11-06.json) | [CycloneDX](sbom/recall-2026-11-06.cdx.json) — **superseded** by the 2026-11-09 SBOMs (the lock has changed since) |

To regenerate an SBOM, see "Deterministic results" in the
[2026-11-09 report](report-recall-2026-11-09.md#deterministic-results).

See also the living security docs: [../docs/THREAT_MODEL.md](../docs/THREAT_MODEL.md)
and [../docs/ADVERSARIAL_REVIEW.md](../docs/ADVERSARIAL_REVIEW.md).
