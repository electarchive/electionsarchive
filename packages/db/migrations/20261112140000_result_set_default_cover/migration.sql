create index if not exists result_set_default_cover_idx on result_set (contest_id) include (id, stage, source_id) where is_default;
