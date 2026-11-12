-- The runners wrap each migration in a transaction, so the unique index cannot be built concurrently.
alter table live.value add column channel smallint not null default 0;--> statement-breakpoint
alter table live.value add constraint value_channel_fkey foreign key (channel) references public.channel (id) not valid;--> statement-breakpoint
alter table live.value validate constraint value_channel_fkey;--> statement-breakpoint
alter table live.value drop constraint value_pkey, add primary key (frame_k, unit_id, option_id, channel);
