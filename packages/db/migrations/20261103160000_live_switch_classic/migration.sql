alter table live.switch drop constraint switch_name_check;--> statement-breakpoint
alter table live.switch add constraint switch_name_check check (name in ('results', 'forecasts', 'classic'));--> statement-breakpoint
insert into live.switch (name, shown) values ('classic', false) on conflict (name) do nothing;
