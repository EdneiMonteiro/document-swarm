"""Deterministic execution of a document swarm.

The package separates two kinds of work that the coordinator used to do in the
same sequence of model turns.  Transitions, validation, persistence, retries and
the mechanical checks are code.  Authorship, consolidation, review and audit stay
with agents.  Nothing here assigns a grade or approves a delivery: the gate and
the independent reviewers keep that authority.
"""
