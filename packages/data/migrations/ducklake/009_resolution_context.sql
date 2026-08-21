-- Name the stored resolver comparison values after their actual role. Both
-- tables are rebuildable automatic products; this changes no row contents.

ALTER TABLE episode_hints_auto RENAME COLUMN evidence TO resolution_context;
ALTER TABLE episode_binding_proposals
    RENAME COLUMN proposal_evidence TO resolution_context;
