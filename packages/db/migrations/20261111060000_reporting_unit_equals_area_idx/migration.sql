create index if not exists reporting_unit_equals_area_idx on reporting_unit (area_id) include (k) where area_relation = 'equals';
