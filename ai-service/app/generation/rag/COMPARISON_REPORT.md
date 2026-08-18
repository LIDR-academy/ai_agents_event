# Chunking comparison report

> Generado por `scripts/compare_chunkers.py --output ...` como red de seguridad del directo. Números reales del corpus instrumentado.

## Estadísticos por estrategia

| strategy | n_chunks | min | p50 | p95 | max | orphans (<20) | obese (>800) | cost_usd | seconds |
|---|---|---|---|---|---|---|---|---|---|
| structural | 60 | 57 | 78.0 | 96.5 | 1391 | 0 | 1 | 0.000000 | 0.00 |
| fixed_size | 63 | 57 | 79.0 | 108.7 | 512 | 0 | 0 | 0.000000 | 0.00 |
| recursive | 23 | 10 | 187.0 | 488.5 | 494 | 1 | 0 | 0.000000 | 0.01 |
| sentence_window | 78 | 3 | 21.0 | 82.0 | 101 | 34 | 0 | 0.000000 | 0.01 |
| semantic | 34 | 57 | 112.5 | 165.4 | 1278 | 0 | 1 | 0.000094 | 5.31 |
| propositional | 778 | 6 | 11.0 | 17.0 | 26 | 765 | 0 | 0.007550 | 173.41 |
| contextual_retrieval | 60 | 103 | 132.5 | 160.7 | 1457 | 0 | 1 | 0.140155 | 202.72 |
| hierarchical | 77 | 57 | 81.0 | 236.8 | 1472 | 0 | 2 | 0.000000 | 0.01 |

## Top-k por consulta y estrategia

### OAuth authentication for fintech mobile app

- **structural**: BUD-2024-001::AUTH-001 (0.568) · BUD-2024-001::NOTIF-004 (0.531) · BUD-2024-001::PSD2-002 (0.522)
- **fixed_size**: BUD-2024-001::AUTH-001::p0 (0.568) · BUD-2024-001::NOTIF-004::p0 (0.531) · BUD-2024-001::PSD2-002::p0 (0.522)
- **recursive**: BUD-2024-001::r0 (0.503) · BUD-2024-003::r0 (0.340) · BUD-2024-016::r0 (0.338)
- **sentence_window**: BUD-2024-001::PSD2-002::s0 (0.453) · BUD-2024-017::MVP-001::s0 (0.441) · BUD-2024-006::VENDOR-001::s0 (0.411)
- **semantic**: BUD-2024-001::sem0 (0.565) · BUD-2024-003::sem0 (0.394) · BUD-2024-004::sem1 (0.377)
- **propositional**: BUD-2024-001::PSD2-002::prop1 (0.634) · BUD-2024-001::TXN-003::prop1 (0.619) · BUD-2024-001::NOTIF-004::prop1 (0.619)
- **contextual_retrieval**: BUD-2024-001::AUTH-001 (0.607) · BUD-2024-001::PSD2-002 (0.517) · BUD-2024-001::TXN-003 (0.497)
- **hierarchical**: BUD-2024-001::AUTH-001 (0.568) · BUD-2024-001::NOTIF-004 (0.531) · BUD-2024-001::PSD2-002 (0.522)

### real-time inventory synchronization

- **structural**: BUD-2024-007::INV-003 (0.533) · BUD-2024-011::STOCK-002 (0.463) · BUD-2024-008::RESALE-003 (0.462)
- **fixed_size**: BUD-2024-007::INV-003::p0 (0.533) · BUD-2024-011::STOCK-002::p0 (0.463) · BUD-2024-008::RESALE-003::p0 (0.462)
- **recursive**: BUD-2024-007::r0 (0.382) · BUD-2024-011::r0 (0.375) · BUD-2024-016::r3 (0.358)
- **sentence_window**: BUD-2024-007::INV-003::s0 (0.739) · BUD-2024-008::RESALE-003::s0 (0.602) · BUD-2024-011::STOCK-002::s0 (0.519)
- **semantic**: BUD-2024-007::sem1 (0.611) · BUD-2024-008::sem1 (0.506) · BUD-2024-011::sem0 (0.415)
- **propositional**: BUD-2024-007::INV-003::prop5 (0.823) · BUD-2024-007::INV-003::prop4 (0.814) · BUD-2024-007::INV-003::prop6 (0.738)
- **contextual_retrieval**: BUD-2024-007::INV-003 (0.529) · BUD-2024-011::STOCK-002 (0.460) · BUD-2024-008::RESALE-003 (0.423)
- **hierarchical**: BUD-2024-007::INV-003 (0.533) · BUD-2024-011::STOCK-002 (0.463) · BUD-2024-008::RESALE-003 (0.462)

### GDPR compliance and audit logging

- **structural**: BUD-2024-009::CONSENT-004 (0.418) · BUD-2024-012::EXPORT-003 (0.301) · BUD-2024-001::TXN-003 (0.289)
- **fixed_size**: BUD-2024-009::CONSENT-004::p0 (0.418) · BUD-2024-012::EXPORT-003::p0 (0.301) · BUD-2024-001::TXN-003::p0 (0.289)
- **recursive**: BUD-2024-012::r0 (0.308) · BUD-2024-001::r0 (0.273) · BUD-2024-009::r0 (0.269)
- **sentence_window**: BUD-2024-009::CONSENT-004::s0 (0.617) · BUD-2024-016::MONO-001::s17 (0.421) · BUD-2024-001::PSD2-002::s0 (0.385)
- **semantic**: BUD-2024-009::sem1 (0.358) · BUD-2024-002::sem1 (0.297) · BUD-2024-012::sem0 (0.296)
- **propositional**: BUD-2024-009::CONSENT-004::prop5 (0.673) · BUD-2024-009::FHIR-001::prop9 (0.554) · BUD-2024-016::MONO-001::prop83 (0.543)
- **contextual_retrieval**: BUD-2024-009::CONSENT-004 (0.465) · BUD-2024-012::EXPORT-003 (0.307) · BUD-2024-001::PSD2-002 (0.304)
- **hierarchical**: BUD-2024-009::CONSENT-004 (0.418) · BUD-2024-012::parent (0.307) · BUD-2024-012::EXPORT-003 (0.301)

### database performance optimization

- **structural**: BUD-2024-015::METER-001 (0.300) · BUD-2024-001::TXN-003 (0.291) · BUD-2024-015::DR-002 (0.289)
- **fixed_size**: BUD-2024-016::MONO-001::p1 (0.316) · BUD-2024-016::MONO-001::p2 (0.311) · BUD-2024-015::METER-001::p0 (0.300)
- **recursive**: BUD-2024-016::r5 (0.355) · BUD-2024-016::r1 (0.310) · BUD-2024-016::r3 (0.305)
- **sentence_window**: BUD-2024-016::MONO-001::s2 (0.363) · BUD-2024-014::KPI-004::s0 (0.355) · BUD-2024-016::MONO-001::s1 (0.337)
- **semantic**: BUD-2024-001::sem1 (0.329) · BUD-2024-005::sem1 (0.321) · BUD-2024-014::sem1 (0.313)
- **propositional**: BUD-2024-016::MONO-001::prop18 (0.368) · BUD-2024-012::EDC-001::prop7 (0.350) · BUD-2024-004::MKT-002::prop7 (0.343)
- **contextual_retrieval**: BUD-2024-015::METER-001 (0.269) · BUD-2024-016::MONO-001 (0.266) · BUD-2024-001::TXN-003 (0.264)
- **hierarchical**: BUD-2024-015::METER-001 (0.300) · BUD-2024-015::parent (0.300) · BUD-2024-004::parent (0.291)

### frontend dashboard with charts

- **structural**: BUD-2024-010::CARE-003 (0.475) · BUD-2024-003::DASH-004 (0.454) · BUD-2024-013::SCADA-003 (0.428)
- **fixed_size**: BUD-2024-010::CARE-003::p0 (0.474) · BUD-2024-003::DASH-004::p0 (0.454) · BUD-2024-013::SCADA-003::p0 (0.428)
- **recursive**: BUD-2024-010::r0 (0.364) · BUD-2024-005::r0 (0.354) · BUD-2024-013::r0 (0.343)
- **sentence_window**: BUD-2024-010::CARE-003::s0 (0.520) · BUD-2024-003::DASH-004::s0 (0.475) · BUD-2024-013::SCADA-003::s0 (0.419)
- **semantic**: BUD-2024-010::sem1 (0.508) · BUD-2024-013::sem1 (0.433) · BUD-2024-005::sem1 (0.373)
- **propositional**: BUD-2024-003::DASH-004::prop6 (0.535) · BUD-2024-010::CARE-003::prop4 (0.529) · BUD-2024-010::CARE-003::prop8 (0.522)
- **contextual_retrieval**: BUD-2024-010::CARE-003 (0.502) · BUD-2024-003::DASH-004 (0.459) · BUD-2024-013::SCADA-003 (0.449)
- **hierarchical**: BUD-2024-010::CARE-003 (0.475) · BUD-2024-003::DASH-004 (0.454) · BUD-2024-013::SCADA-003 (0.428)

### payment gateway integration

- **structural**: BUD-2024-003::GATE-001 (0.547) · BUD-2024-003::DASH-004 (0.507) · BUD-2024-003::RECON-003 (0.490)
- **fixed_size**: BUD-2024-003::GATE-001::p0 (0.547) · BUD-2024-003::DASH-004::p0 (0.507) · BUD-2024-003::RECON-003::p0 (0.490)
- **recursive**: BUD-2024-003::r0 (0.482) · BUD-2024-006::r0 (0.376) · BUD-2024-001::r0 (0.345)
- **sentence_window**: BUD-2024-003::GATE-001::s0 (0.528) · BUD-2024-006::PAYOUT-003::s0 (0.499) · BUD-2024-005::CART-002::s0 (0.458)
- **semantic**: BUD-2024-003::sem0 (0.512) · BUD-2024-006::sem1 (0.403) · BUD-2024-006::sem0 (0.403)
- **propositional**: BUD-2024-003::GATE-001::prop6 (0.630) · BUD-2024-003::GATE-001::prop9 (0.624) · BUD-2024-003::GATE-001::prop11 (0.617)
- **contextual_retrieval**: BUD-2024-003::GATE-001 (0.530) · BUD-2024-003::DASH-004 (0.482) · BUD-2024-003::RECON-003 (0.465)
- **hierarchical**: BUD-2024-003::GATE-001 (0.547) · BUD-2024-003::DASH-004 (0.507) · BUD-2024-003::RECON-003 (0.490)

