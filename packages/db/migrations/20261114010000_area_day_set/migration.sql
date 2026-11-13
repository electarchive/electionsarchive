create table area_day_scope (area_k integer primary key);
--> statement-breakpoint
create table area_day_set (area_k integer not null, day date not null, result_set_k integer not null, voted boolean not null, primary key (area_k, day, result_set_k) include (voted));
--> statement-breakpoint
create index area_day_set_result_set_k_idx on area_day_set (result_set_k);
--> statement-breakpoint
comment on column area_day_scope.area_k is 'references area (k)';
--> statement-breakpoint
comment on column area_day_set.area_k is 'references area (k)';
--> statement-breakpoint
comment on column area_day_set.result_set_k is 'references result_set (k)';
