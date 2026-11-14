create index if not exists candidacy_head_option_idx2 on candidacy (option_id) include (person_id, id, is_independent, is_incumbent) where role = 'head';
drop index if exists candidacy_head_option_idx;
