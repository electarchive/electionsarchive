delete from live.switch where name = 'classic';--> statement-breakpoint
alter table live.switch drop constraint switch_name_check;--> statement-breakpoint
alter table live.switch add constraint switch_name_check check (name in ('results', 'forecasts'));
