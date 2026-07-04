# Baseline null identity

The dispatcher requires a closed track to run; "no identity" means the NULL
identity: four content-free diagnostic routes (diag.echo, diag.relay,
diag.report, plus config.update for the authority-signature path) and two
probe spokes (p1, p2). No vertical text, no domain rules.

Purpose: pillar validation and BASELINE KPI capture on a bare dispatcher.
Numbers produced on this identity are the reference every vertical identity
(listing-agents first) is compared against. See PILLAR_TESTING_MANUAL.md.
