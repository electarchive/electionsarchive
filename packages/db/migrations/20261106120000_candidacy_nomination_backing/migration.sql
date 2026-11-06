create index if not exists candidacy_nomination_backing_idx on candidacy_nomination (candidacy_id, party_id) where kind in ('endorsed', 'supported');
