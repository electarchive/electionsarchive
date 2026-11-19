CREATE OR REPLACE FUNCTION public.ea_version(tbl regclass)
 RETURNS void
 LANGUAGE plpgsql
AS $function$
declare
  capture text := tbl::text || '_capture';
begin
  execute format(
    'create table %s_history (like %s, recorded tstzrange not null, superseded_by uuid not null)',
    tbl, tbl
  );
  execute format(
    $f$create function %I() returns trigger language plpgsql as $b$
       begin
         insert into %s_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $b$ $f$,
    capture, tbl
  );
  execute format('create trigger ea_stamp before insert or update on %s for each row execute function ea_stamp()', tbl);
  execute format(
    'create trigger ea_history_update after update on %s referencing old table as old_rows'
    ' for each statement execute function %I()',
    tbl, capture
  );
  execute format(
    'create trigger ea_history_delete after delete on %s referencing old table as old_rows'
    ' for each statement execute function %I()',
    tbl, capture
  );
end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.alliance_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into alliance_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.alliance_member_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into alliance_member_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.area_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into area_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.area_code_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into area_code_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.area_event_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into area_event_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.area_event_member_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into area_event_member_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.area_figure_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into area_figure_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.area_geometry_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
begin
  insert into area_geometry_history
  select o.area_id, o.valid_from, o.valid_from_precision, o.valid_to, o.valid_to_precision, o.valid,
         case when exists (
           select from area_geometry g where g.area_id = o.area_id and md5(st_asbinary(g.geom)) = md5(st_asbinary(o.geom))
         ) then null else o.geom end,
         o.label_point, o.quality, o.source_id, o.changeset_id, o.recorded_from,
         tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset(), md5(st_asbinary(o.geom))::uuid
  from old_rows o;
  return null;
end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.area_kind_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into area_kind_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.area_membership_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into area_membership_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.area_parent_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into area_parent_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.area_universe_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into area_universe_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.ballot_question_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into ballot_question_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.ballot_question_text_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into ballot_question_text_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.body_apportionment_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into body_apportionment_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.body_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into body_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.body_chamber_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into body_chamber_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.body_composition_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into body_composition_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.body_composition_party_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into body_composition_party_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.body_event_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into body_event_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.body_size_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into body_size_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.body_size_part_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into body_size_part_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.boundary_set_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into boundary_set_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.candidacy_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into candidacy_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.candidacy_nomination_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into candidacy_nomination_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.census_class_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into census_class_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.census_date_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into census_date_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.census_match_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into census_match_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.census_tier_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into census_tier_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.census_tier_count_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into census_tier_count_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.census_world_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
begin
  insert into census_world_history (
    iso_code, name_en, status, within_iso, reason, electorate, electorate_date, electorate_precision,
    electorate_source_id, electorate_locator, source_id, locator, changeset_id, recorded_from,
    recorded, superseded_by, recognized_within
  )
  select
    o.iso_code, o.name_en, o.status, o.within_iso, o.reason, o.electorate, o.electorate_date, o.electorate_precision,
    o.electorate_source_id, o.electorate_locator, o.source_id, o.locator, o.changeset_id, o.recorded_from,
    tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset(), o.recognized_within
  from old_rows o;
  return null;
end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.citation_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into citation_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.contest_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into contest_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.contest_option_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into contest_option_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.crosswalk_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into crosswalk_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.ea_stamp()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
begin
  if tg_op = 'UPDATE' and current_setting('ea.repointing', true) = 'on' then
    new.changeset_id := old.changeset_id;
  else
    new.changeset_id := ea_current_changeset();
  end if;
  new.recorded_from := case when tg_op = 'UPDATE' then greatest(now(), old.recorded_from) else now() end;
  return new;
end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.ea_stamp_result_set()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
begin
  if tg_op = 'UPDATE' and (
       current_setting('ea.repointing', true) = 'on'
       or old.is_default is distinct from new.is_default
          and to_jsonb(old) - 'is_default' - 'changeset_id' - 'recorded_from'
            = to_jsonb(new) - 'is_default' - 'changeset_id' - 'recorded_from') then
    new.changeset_id := old.changeset_id;
  else
    new.changeset_id := ea_current_changeset();
  end if;
  new.recorded_from := case when tg_op = 'UPDATE' then greatest(now(), old.recorded_from) else now() end;
  return new;
end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.election_cadence_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
begin
  insert into election_cadence_history (id, polity_id, government_kind, function, name_pattern, body_id, office_id, selection, period_years, phase, month, day_rule, classes, term_years, partisan, runoff, runoff_threshold, runoff_rule, administered_by, valid_from, valid_to, source_id, locator, changeset_id, recorded_from, class_id, electoral_system_id, called, vacancy_rule, certify_days, recorded, superseded_by)
  select id, polity_id, government_kind, function, name_pattern, body_id, office_id, selection, period_years, phase, month, day_rule, classes, term_years, partisan, runoff, runoff_threshold, runoff_rule, administered_by, valid_from, valid_to, source_id, locator, changeset_id, recorded_from, class_id, electoral_system_id, called, vacancy_rule, certify_days, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
  return null;
end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.election_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into election_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.electoral_system_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into electoral_system_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.entity_merge_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into entity_merge_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.entity_merge_drop_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into entity_merge_drop_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.entity_retirement_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into entity_retirement_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.entity_subtype_drop_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into entity_subtype_drop_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.external_id_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into external_id_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.external_id_drop_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into external_id_drop_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.finance_summary_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into finance_summary_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.flag_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into flag_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.government_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into government_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.government_party_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into government_party_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.indicator_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into indicator_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.label_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into label_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.membership_drop_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into membership_drop_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.name_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into name_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.name_drop_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into name_drop_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.note_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into note_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.note_drop_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into note_drop_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.office_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into office_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.option_drop_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into option_drop_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.outcome_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into outcome_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.party_affiliation_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into party_affiliation_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.party_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into party_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.party_color_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into party_color_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.party_founding_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into party_founding_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.party_image_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into party_image_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.party_position_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into party_position_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.party_relation_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into party_relation_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.person_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into person_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.person_image_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into person_image_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.person_party_membership_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into person_party_membership_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.polity_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into polity_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.polity_parent_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into polity_parent_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.polity_period_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into polity_period_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.polity_relation_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into polity_relation_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.polity_territory_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into polity_territory_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.reporting_unit_area_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into reporting_unit_area_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.reporting_unit_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into reporting_unit_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.representation_gap_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into representation_gap_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.result_set_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into result_set_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.result_set_drop_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into result_set_drop_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.results_access_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
begin
  insert into results_access_history (id, polity_id, serves, platform, url_pattern, format, access, first_date, last_date, checked_at, evidence, valid_from, valid_to, source_id, changeset_id, recorded_from, class_id, role, license, finest_scheme_code, boundary_source_id, recorded, superseded_by)
  select id, polity_id, serves, platform, url_pattern, format, access, first_date, last_date, checked_at, evidence, valid_from, valid_to, source_id, changeset_id, recorded_from, class_id, role, license, finest_scheme_code, boundary_source_id, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
  return null;
end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.row_drop_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into row_drop_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.scheme_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into scheme_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.seat_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into seat_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.slug_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into slug_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.source_artifact_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into source_artifact_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.source_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into source_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.source_page_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into source_page_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.tenure_affiliation_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into tenure_affiliation_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.tenure_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into tenure_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.tenure_event_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into tenure_event_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.translation_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into translation_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.unit_area_drop_capture()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
       begin
         insert into unit_area_drop_history select o.*, tstzrange(least(o.recorded_from, now()), now()), ea_current_changeset() from old_rows o;
         return null;
       end $function$
;
