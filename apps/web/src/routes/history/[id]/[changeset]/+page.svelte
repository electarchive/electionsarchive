<script lang="ts">
  import { importedBy, roleLabel } from "#lib/changes.js";
  import Moment from "#lib/components/moment.svelte";
  import { m } from "#lib/paraglide/messages.js";
  import { moment } from "#lib/zone.svelte.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();

  const tables: Record<string, () => string> = {
    entity: m.history_table_entity,
    name: m.history_table_name,
    citation: m.history_table_citation,
    note: m.history_table_note,
    translation: m.history_table_translation,
    external_id: m.history_table_external_id,
    party_color: m.history_table_party_color,
    party_image: m.history_table_party_image,
    person_image: m.history_table_person_image,
    result_set: m.history_table_result_set,
    outcome: m.history_table_outcome,
    label: m.history_table_label,
  };
  const ops = {
    add: m.history_op_add,
    change: m.history_op_change,
    drop: m.history_op_drop,
  };

  const tableName = (table: string) =>
    tables[table]?.() ??
    `${table.charAt(0).toUpperCase()}${table.slice(1).replaceAll("_", " ")}`;
  const shown = (value: unknown) =>
    typeof value === "string"
      ? (data.names[value] ?? value)
      : JSON.stringify(value);
</script>

<svelte:head>
  <title
    >{m.history_change_title({
      summary: data.entry.summary,
      name: data.entity.name,
    })}</title
  >
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="mx-auto px-4 pbs-6 pbe-24 max-inline-3xl max-sm:px-3">
  <header class="grid gap-3">
    <p class="text-sm">
      <a
        class="underline underline-offset-4"
        href="/history/{data.entity.public_id}"
        >{m.history_back({ name: data.entity.name })}</a
      >
    </p>
    <h1 class="title text-balance">{data.entry.summary}</h1>
    <p
      class="flex flex-wrap gap-x-4 gap-y-1 text-sm text-pretty text-muted-foreground"
    >
      <span class="font-semibold text-foreground"
        >{roleLabel[data.entry.role]()}</span
      >
      <Moment
        at={data.entry.at}
        label={m.history_published({
          date: moment(data.entry.at, "date"),
          time: moment(data.entry.at, "time"),
        })}
      />
      <span>{importedBy(data.entry.pipeline)}</span>
    </p>
  </header>

  <section class="mbs-8" aria-labelledby="fields">
    <h2 id="fields" class="heading">{m.history_fields()}</h2>
    {#if data.changes.length === 0}
      <p
        class="mbs-2 border-bs pbs-3 text-sm text-pretty text-muted-foreground"
      >
        {m.history_no_fields()}
      </p>
    {:else}
      <ul class="mbs-2 grid border-bs text-sm">
        {#each data.changes as item, i (i)}
          <li
            class="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] gap-x-4 border-be py-2 max-sm:grid-cols-1"
          >
            <span class="text-muted-foreground"
              >{tableName(item.table)}<span
                class="block text-xs font-semibold text-foreground"
                >{ops[item.op]()}</span
              ></span
            >
            <dl class="grid gap-1 min-inline-0">
              {#each item.fields as field (field.field)}
                <div class="flex flex-wrap items-baseline gap-x-2">
                  <dt class="text-xs text-muted-foreground">
                    {field.field.replaceAll("_", " ")}
                  </dt>
                  <dd
                    class="flex flex-wrap items-baseline gap-x-2 min-inline-0"
                  >
                    {#if item.op === "change"}
                      <del class="wrap-anywhere text-muted-foreground"
                        >{field.before == null
                          ? m.history_empty_value()
                          : shown(field.before)}</del
                      >
                      <span aria-hidden="true" class="text-muted-foreground"
                        >→</span
                      >
                    {/if}
                    {#if item.op === "drop"}
                      <del class="wrap-anywhere">{shown(field.before)}</del>
                    {:else}
                      <ins class="wrap-anywhere no-underline"
                        >{field.after == null
                          ? m.history_empty_value()
                          : shown(field.after)}</ins
                      >
                    {/if}
                  </dd>
                </div>
              {/each}
            </dl>
          </li>
        {/each}
      </ul>
    {/if}
  </section>
</main>
