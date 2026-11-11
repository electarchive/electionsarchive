create index if not exists name_entity_cover_idx on name (entity_id) include (kind, is_preferred, lang, script, valid, recorded_from, id, text);
drop index if exists name_entity_idx;
