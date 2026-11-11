create index if not exists candidacy_head_option_idx on candidacy (option_id) include (person_id, id) where role = 'head';
