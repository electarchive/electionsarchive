create index if not exists result_set_election_cover_idx on result_set (election_id) include (k, contest_id, status, source_id);
