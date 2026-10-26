set lock_timeout = '5s';

alter table scheme drop constraint scheme_code_check;
alter table scheme add constraint scheme_code_check check (code ~ '^([a-z]{2,3}(-[a-z0-9]+)?|world)\.[a-z0-9_]+$');
alter table scheme alter column polity_id drop not null;
alter table scheme add constraint scheme_polity_check check (polity_id is not null or code like 'world.%');
alter table scheme_history alter column polity_id drop not null;
