def test_aggregate_evidence_alone_counts_as_registered_target_bound_evidence():
    """A count is evidence: the proxy matches an exact total_count for a targeted request.

    The live runner's seed gate asked only for sentinels and targets, so a read task whose
    answer *is* a count could register its evidence and still be rejected as having registered
    none. L2 failed that way on every repetition of the first full-catalog battery, after the
    seeding bug that had hidden it was fixed.
    """
    from evals.evidence import TARGET_ENTITY_EVIDENCE, configured_evidence_labels

    targets = {TARGET_ENTITY_EVIDENCE: ("wi-1",)}
    aggregates = {TARGET_ENTITY_EVIDENCE: ({"kind": "total_count", "value": 3},)}

    assert configured_evidence_labels(None, targets, aggregates) == (TARGET_ENTITY_EVIDENCE,)
    # Targets alone are still not evidence, and neither is an aggregate with nothing to bind to.
    assert configured_evidence_labels(None, targets, None) == ()
    assert configured_evidence_labels(None, None, aggregates) == ()
