create index if not exists election_date_kind_idx on election (date_from, kind) include (polity_id, id);
