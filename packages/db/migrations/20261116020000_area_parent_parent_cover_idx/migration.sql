create index if not exists area_parent_parent_cover_idx on area_parent (parent_id) include (area_id, valid);
