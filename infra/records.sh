#!/bin/bash
cd "$HOME/electionsarchive/pipelines" || exit 1
late=$(psql electionsarchive -tAc "select count(*) from changeset where status = 'published' and published_at > (select coalesce(min(built_at), '-infinity') from polity_record)")
[ "$late" = 0 ] && { echo "polity records are newer than every publish"; exit 0; }
../data/logs/gate.sh uv run ea records || exit $?
base=${EA_WARM_URL:-http://127.0.0.1:5173}
psql electionsarchive -tAc "select polity_id from polity_record r left join slug s on s.entity_id = r.polity_id and s.is_current and s.path = 'us' order by (s.path is not null) desc, pg_column_size(record) desc limit 12" |
  while read -r id; do
    data=$(printf '{"json":{"id":"%s"}}' "$id" | jq -sRr @uri)
    curl -s -o /dev/null -m 300 -H 'x-ea-refresh: 1' "$base/rpc/polities/record?data=$data" || echo "warm-up of $id failed"
  done
exit 0
