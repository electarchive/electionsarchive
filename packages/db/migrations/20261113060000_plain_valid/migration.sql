lock table area_code, area_geometry, area_kind, area_membership, area_parent, area_universe, body_apportionment, body_chamber, body_size, body_size_part, party_affiliation, party_color, party_position, person_party_membership, polity_parent, polity_territory, reporting_unit_area, representation_gap, tenure_affiliation in access exclusive mode;
--> statement-breakpoint
create function ea_valid() returns trigger language plpgsql as $$
begin
  new.valid := daterange(new.valid_from, new.valid_to, '[)');
  return new;
end $$;
--> statement-breakpoint
alter table area_code alter column valid drop expression;
--> statement-breakpoint
create trigger ea_valid before insert or update on area_code for each row execute function ea_valid();
--> statement-breakpoint
alter table area_geometry alter column valid drop expression;
--> statement-breakpoint
create trigger ea_valid before insert or update on area_geometry for each row execute function ea_valid();
--> statement-breakpoint
alter table area_kind alter column valid drop expression;
--> statement-breakpoint
create trigger ea_valid before insert or update on area_kind for each row execute function ea_valid();
--> statement-breakpoint
alter table area_membership alter column valid drop expression;
--> statement-breakpoint
create trigger ea_valid before insert or update on area_membership for each row execute function ea_valid();
--> statement-breakpoint
alter table area_parent alter column valid drop expression;
--> statement-breakpoint
create trigger ea_valid before insert or update on area_parent for each row execute function ea_valid();
--> statement-breakpoint
alter table area_universe alter column valid drop expression;
--> statement-breakpoint
create trigger ea_valid before insert or update on area_universe for each row execute function ea_valid();
--> statement-breakpoint
alter table body_apportionment alter column valid drop expression;
--> statement-breakpoint
create trigger ea_valid before insert or update on body_apportionment for each row execute function ea_valid();
--> statement-breakpoint
alter table body_chamber alter column valid drop expression;
--> statement-breakpoint
create trigger ea_valid before insert or update on body_chamber for each row execute function ea_valid();
--> statement-breakpoint
alter table body_size alter column valid drop expression;
--> statement-breakpoint
create trigger ea_valid before insert or update on body_size for each row execute function ea_valid();
--> statement-breakpoint
alter table body_size_part alter column valid drop expression;
--> statement-breakpoint
create trigger ea_valid before insert or update on body_size_part for each row execute function ea_valid();
--> statement-breakpoint
alter table party_affiliation alter column valid drop expression;
--> statement-breakpoint
create trigger ea_valid before insert or update on party_affiliation for each row execute function ea_valid();
--> statement-breakpoint
alter table party_color alter column valid drop expression;
--> statement-breakpoint
create trigger ea_valid before insert or update on party_color for each row execute function ea_valid();
--> statement-breakpoint
alter table party_position alter column valid drop expression;
--> statement-breakpoint
create trigger ea_valid before insert or update on party_position for each row execute function ea_valid();
--> statement-breakpoint
alter table person_party_membership alter column valid drop expression;
--> statement-breakpoint
create trigger ea_valid before insert or update on person_party_membership for each row execute function ea_valid();
--> statement-breakpoint
alter table polity_parent alter column valid drop expression;
--> statement-breakpoint
create trigger ea_valid before insert or update on polity_parent for each row execute function ea_valid();
--> statement-breakpoint
alter table polity_territory alter column valid drop expression;
--> statement-breakpoint
create trigger ea_valid before insert or update on polity_territory for each row execute function ea_valid();
--> statement-breakpoint
alter table reporting_unit_area alter column valid drop expression;
--> statement-breakpoint
create trigger ea_valid before insert or update on reporting_unit_area for each row execute function ea_valid();
--> statement-breakpoint
alter table representation_gap alter column valid drop expression;
--> statement-breakpoint
create trigger ea_valid before insert or update on representation_gap for each row execute function ea_valid();
--> statement-breakpoint
alter table tenure_affiliation alter column valid drop expression;
--> statement-breakpoint
create trigger ea_valid before insert or update on tenure_affiliation for each row execute function ea_valid();
--> statement-breakpoint
alter table person_party_membership replica identity full;
--> statement-breakpoint
alter table tenure_affiliation replica identity full;
