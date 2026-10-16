create index contest_previous_round_idx on contest (previous_round_id);
create index contest_option_parent_option_idx on contest_option (parent_option_id);
create index reporting_unit_counted_with_idx on reporting_unit (counted_with_id);
create index result_set_supersedes_idx on result_set (supersedes_id);
create index search_document_polity_idx on search_document (polity_id);
create index citation_entity_idx on citation (entity_id);
create index flag_entity_idx on flag (entity_id);
create index flag_result_set_idx on flag (result_set_id);
