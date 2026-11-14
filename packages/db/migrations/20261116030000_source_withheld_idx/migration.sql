create index if not exists source_withheld_idx on source (id) where license ilike '%ICPSR%' or license ilike 'Withdrawn:%';
