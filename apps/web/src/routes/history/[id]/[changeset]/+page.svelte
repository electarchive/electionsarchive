<script lang="ts">
  import { enhance } from "$app/forms";
  import RecordTabs from "#lib/components/contrib/record-tabs.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Textarea } from "#lib/components/ui/textarea/index.js";
  import { historyByline, roleLabel, stateLabel } from "#lib/contrib.js";
  import { date, dates } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { ActionData, PageData } from "./$types";

  const { data, form }: { data: PageData; form: ActionData } = $props();

  const clock = dates({ hour: "numeric", minute: "2-digit", timeZone: "UTC" });

  const tables: Record<string, () => string> = {
    entity: m.history_table_entity,
    name: m.proposal_table_name,
    citation: m.proposal_table_citation,
    note: m.proposal_table_note,
    translation: m.proposal_table_translation,
    external_id: m.proposal_table_external_id,
    party_color: m.proposal_table_party_color,
    party_image: m.proposal_table_party_image,
    person_image: m.proposal_table_person_image,
    result_set: m.proposal_table_result_set,
    outcome: m.proposal_table_outcome,
    label: m.proposal_table_label,
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

  let isPending = $state(false);
</script>

<svelte:head>
  <title
    >{m.history_change_title({
      summary: data.entry.title ?? data.entry.summary,
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
    <h1 class="title text-balance">{data.entry.title ?? data.entry.summary}</h1>
    <RecordTabs entity={data.entity} current="history" />
    <p
      class="flex flex-wrap gap-x-4 gap-y-1 text-sm text-pretty text-muted-foreground"
    >
      <span class="font-semibold text-foreground"
        >{roleLabel[data.entry.role]()}</span
      >
      <time datetime={data.entry.at}
        >{m.history_published({
          date: date(data.entry.at.slice(0, 10)),
          time: clock.format(new Date(data.entry.at)),
        })}</time
      >
      {#if data.entry.handle == null}
        <span>{historyByline(data.entry)}</span>
      {:else}
        <a
          class="underline-offset-4 hover:text-foreground hover:underline"
          href="/contributors/{data.entry.handle}"
          >{historyByline(data.entry)}</a
        >
      {/if}
      {#if data.entry.number != null}
        <a
          class="underline-offset-4 hover:text-foreground hover:underline"
          href="/proposals/{data.entry.number}"
          >{m.history_proposal({ number: data.entry.number })}</a
        >
        {#if data.entry.state != null && data.entry.state !== "published"}
          <span>{stateLabel[data.entry.state]()}</span>
        {/if}
      {/if}
    </p>
    {#if data.entry.title != null && data.entry.title !== data.entry.summary}
      <p class="text-sm text-pretty">{data.entry.summary}</p>
    {/if}
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

  {#if data.revertible && data.entry.number != null}
    <section class="mbs-10" aria-labelledby="revert">
      <h2 id="revert" class="heading">{m.history_revert_heading()}</h2>
      {#if form != null && "queued" in form}
        <p class="mbs-2 text-sm" role="status">{m.history_revert_queued()}</p>
      {:else}
        <p
          class="max-inline-prose mbs-2 text-sm text-pretty text-muted-foreground"
        >
          {m.history_revert_body()}
        </p>
        <form
          method="POST"
          action="?/revert"
          class="mbs-3 grid gap-2 max-inline-xl"
          use:enhance={() => {
            isPending = true;
            return async ({ update }) => {
              await update();
              isPending = false;
            };
          }}
        >
          <label class="text-sm font-medium" for="revert-reason"
            >{m.history_revert_reason()}</label
          >
          <Textarea id="revert-reason" name="reason" required minlength={3} />
          {#if form != null && "message" in form}
            <p class="text-sm text-destructive" role="alert">{form.message}</p>
          {/if}
          <div>
            <Button type="submit" variant="outline" disabled={isPending}
              >{m.history_revert_submit({ number: data.entry.number })}</Button
            >
          </div>
        </form>
      {/if}
    </section>
  {/if}
</main>
