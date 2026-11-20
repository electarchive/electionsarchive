alter table candidacy validate constraint candidacy_preference_option_id_fkey;
--> statement-breakpoint
alter table candidacy validate constraint candidacy_preference_option_check;
--> statement-breakpoint
create unique index if not exists candidacy_preference_option_key on candidacy (preference_option_id);
--> statement-breakpoint
alter table candidacy
  add constraint candidacy_preference_option_key unique using index candidacy_preference_option_key
  deferrable initially deferred;
