create index if not exists reporting_unit_pooled_idx on reporting_unit (area_id) include (id, k, kind) where area_relation = 'within' and kind <> 'standard';
