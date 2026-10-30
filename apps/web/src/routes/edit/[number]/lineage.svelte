<script lang="ts">
  import { resolve } from "$app/paths";
  import PlusIcon from "@lucide/svelte/icons/plus";
  import XIcon from "@lucide/svelte/icons/x";
  import { tick, untrack } from "svelte";
  import type { Relation } from "#lib/server/workspace.js";
  import type { RelationKind } from "#lib/workspace.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import * as Command from "#lib/components/ui/command/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import * as Select from "#lib/components/ui/select/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import {
    closures,
    relationKinds,
    relationLabel,
    templateTitle,
  } from "#lib/workspace.js";
  import type { PageData } from "./$types";
  import Cite from "./cite.svelte";
  import { none, Records } from "./records.svelte.js";
  import Ref from "./ref.svelte";
  import { Session } from "./session.svelte.js";
  import Shell from "./shell.svelte";

  const {
    data,
    workspace,
  }: {
    data: PageData;
    workspace: Extract<PageData["workspace"], { template: "lineage" }>;
  } = $props();

  type Row = {
    key: number;
    party: string;
    kind: string;
    other: string;
    date: string;
    election: string;
  };

  let next = 0;
  const keyed = (row: Omit<Row, "key">): Row => ({ key: next++, ...row });
  const blank = () =>
    keyed({
      party: "",
      kind: "merged_into",
      other: "",
      date: "",
      election: "",
    });

  const initial = untrack(() => ({ ...data, workspace }));
  let party = $state(initial.workspace.party);
  let rows = $state<Array<Row>>(
    initial.workspace.rows.length === 0
      ? [blank()]
      : initial.workspace.rows.map(keyed),
  );
  let drops = $state<Array<string>>(initial.workspace.drops);
  let summary = $state(initial.summary);
  let cited = $state(initial.workspace.evidence);
  let current = $state<Array<Relation> | null>(null);
  let checkRows = $state<Array<string>>([]);
  let selected = $state<number | null>(null);
  const records = new Records(initial.records, () => [
    party,
    ...rows.flatMap((row) => [row.party, row.other, row.election]),
  ]);

  const focus = $derived(records.look(party, "party"));
  const focusId = $derived(focus.entity?.id ?? null);

  $effect(() => {
    const id = focusId;
    if (id == null) {
      current = null;
      return;
    }
    const controller = new AbortController();
    void (async () => {
      const response = await fetch("/edit/lineage", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ party: id }),
        signal: controller.signal,
      });
      if (!response.ok) {
        return;
      }
      const loaded = (await response.json()) as Array<Relation>;
      if (controller.signal.aborted) {
        return;
      }
      current = loaded;
      drops = drops.filter((drop) => loaded.some((item) => item.id === drop));
    })().catch(() => undefined);
    return () => {
      controller.abort();
    };
  });

  const kindOf = (value: string): RelationKind =>
    relationKinds.find((kind) => kind === value) ?? "merged_into";

  const isWhen = (text: string) => {
    if (!/^\d{4}(?:-\d{2}(?:-\d{2})?)?$/u.test(text)) {
      return false;
    }
    const [year = "", month = "01", day = "01"] = text.split("-");
    const iso = `${year}-${month}-${day}`;
    return (
      !Number.isNaN(Date.parse(iso)) &&
      new Date(`${iso}T00:00:00Z`).toISOString().startsWith(iso)
    );
  };

  const when = (date: string | null, precision: Relation["precision"]) =>
    date == null || precision === "unknown"
      ? ""
      : precision === "year"
        ? date.slice(0, 4)
        : precision === "month"
          ? date.slice(0, 7)
          : date;

  const assessed = $derived.by(() => {
    const seen: Record<string, number> = {};
    return rows.map((row, index) => {
      const n = index + 1;
      const kind = kindOf(row.kind);
      const isClosure = closures.includes(kind);
      const isVehicle = kind === "list_vehicle_for";
      const date = row.date.trim();
      const looked = {
        party: records.look(
          row.party.trim() === "" ? party : row.party,
          "party",
        ),
        other: records.look(row.other, "party"),
        election: isVehicle ? records.look(row.election, "election") : none,
      };
      const isBlank =
        row.other.trim() === "" &&
        date === "" &&
        row.election.trim() === "" &&
        !isClosure;
      const from = looked.party.entity?.id ?? null;
      const to = looked.other.entity?.id ?? null;
      const link = `${from}:${to}:${kind}:${looked.election.entity?.id ?? ""}`;
      const first = seen[link];
      if (from != null && first == null) {
        seen[link] = n;
      }
      const exists =
        !isVehicle &&
        from != null &&
        (current ?? []).some(
          (item) =>
            item.from === from &&
            item.to === to &&
            item.kind === kind &&
            !drops.includes(item.id),
        );
      const problems = {
        party:
          looked.party.problem ??
          (row.party.trim() === "" && party.trim() === "" && !isBlank
            ? m.ws_lineage_needs_party()
            : null),
        other:
          isClosure && row.other.trim() !== ""
            ? m.ws_lineage_no_other()
            : (looked.other.problem ??
              (isBlank
                ? null
                : !isClosure && row.other.trim() === ""
                  ? m.ws_lineage_needs_other()
                  : from != null && from === to
                    ? m.ws_lineage_same()
                    : exists
                      ? m.ws_lineage_exists()
                      : first != null && first !== n
                        ? m.ws_lineage_twice({ row: first })
                        : null)),
        date: date !== "" && !isWhen(date) ? m.ws_lineage_bad_date() : null,
        election:
          looked.election.problem ??
          (isVehicle && !isBlank && row.election.trim() === ""
            ? m.ws_lineage_needs_election()
            : null),
      };
      const problem =
        problems.party ?? problems.other ?? problems.date ?? problems.election;
      const isLooking =
        looked.party.isLooking ||
        looked.other.isLooking ||
        looked.election.isLooking;
      return {
        row,
        n,
        kind,
        isClosure,
        isVehicle,
        isBlank,
        isLooking,
        looked,
        problems,
        problem: isBlank ? null : problem,
        op:
          isBlank || problem != null || isLooking || from == null
            ? null
            : {
                op: "lineage" as const,
                party: from,
                kind,
                ...(to != null && { other: to }),
                ...(date !== "" && { date }),
                ...(isVehicle &&
                  looked.election.entity != null && {
                    election_id: looked.election.entity.id,
                  }),
                evidence: ["e1"] as ["e1"],
              },
      };
    });
  });

  const source = $derived.by(() => {
    if (cited == null) {
      return null;
    }
    const url = cited.url.trim();
    const title = cited.title.trim();
    const publisher = cited.publisher.trim();
    return /^https?:\/\//iu.test(url) &&
      URL.canParse(url) &&
      title !== "" &&
      publisher !== ""
      ? { url, title, publisher, kind: cited.kind }
      : null;
  });

  const links = $derived(
    assessed.flatMap((item) => (item.op == null ? [] : [item.op])),
  );

  const ops = $derived([
    ...drops.map((id) => ({
      op: "drop" as const,
      table: "party_relation" as const,
      id,
      ...(source != null && { evidence: ["e1"] }),
    })),
    ...(source == null ? [] : links),
  ]);

  const order = $derived([
    ...drops.map((id) => `drop:${id}`),
    ...assessed.flatMap((item) =>
      item.op == null ? [] : [`row:${item.row.key}`],
    ),
  ]);

  const compiled = $derived(JSON.stringify([ops, source]));

  const serialized = $derived(
    JSON.stringify({
      summary: summary.trim(),
      ops,
      ...(source != null && { evidence: { e1: source } }),
      workspace: {
        template: "lineage",
        party,
        rows: rows
          .filter(
            (row) =>
              row.other.trim() !== "" ||
              row.date.trim() !== "" ||
              row.election.trim() !== "" ||
              closures.includes(kindOf(row.kind)),
          )
          .map(({ key: _key, ...row }) => row),
        drops,
        evidence: cited,
      },
    }),
  );

  const hasProblem = $derived(assessed.some((item) => item.problem != null));
  const isLookingUp = $derived(
    focus.isLooking || assessed.some((item) => item.isLooking),
  );
  const hasLinks = $derived(assessed.some((item) => !item.isBlank));

  const session = new Session(
    untrack(() => data.number),
    untrack(() => data.check),
    {
      serialized: () => serialized,
      compiled: () => compiled,
      canCheck: () =>
        ops.length > 0 &&
        !hasProblem &&
        !isLookingUp &&
        (!hasLinks || source != null),
      checked: () => {
        checkRows = order;
      },
    },
  );

  checkRows = untrack(() => order);

  const check = $derived(session.check);
  const isLocked = $derived(session.isLocked);

  const failure = $derived.by(() => {
    const error = session.error;
    if (error == null) {
      return null;
    }
    const at = error.op == null ? null : (checkRows[error.op] ?? null);
    const key = at?.startsWith("row:") ? Number(at.slice(4)) : null;
    const index = rows.findIndex((row) => row.key === key);
    return { row: index === -1 ? null : index + 1, text: error.text };
  });

  const blocker = $derived(
    session.blocker(
      hasProblem || failure != null
        ? m.ws_submit_needs_fix()
        : drops.length === 0 && !hasLinks
          ? m.ws_submit_needs_links()
          : hasLinks && source == null
            ? m.ws_lineage_evidence_needed()
            : null,
      summary,
    ),
  );

  const counts = $derived([
    ["party_relation", m.ws_diff_links_added],
    ["row_drop", m.ws_diff_links_removed],
  ] as const);

  const changed = $derived(
    counts.flatMap(([table, label]) => {
      const count = check?.checks?.diff?.[table]?.insert ?? 0;
      return count === 0 ? [] : [{ table, label, count }];
    }),
  );

  const named = (looked: { entity: { name: string } | null }, raw: string) =>
    looked.entity?.name ?? raw.trim();

  async function focusRow(key: number) {
    selected = key;
    await tick();
    document.getElementById(`ws-link-other-${key}`)?.focus();
  }

  function pickRow(n: number) {
    const row = rows[n - 1];
    if (row != null) {
      void focusRow(row.key);
    }
  }

  function addRow() {
    const row = blank();
    rows.push(row);
    void focusRow(row.key);
  }

  function removeRow(key: number) {
    rows = rows.filter((row) => row.key !== key);
  }

  function toggle(id: string) {
    drops = drops.includes(id)
      ? drops.filter((drop) => drop !== id)
      : [...drops, id];
  }
</script>

<Shell
  {session}
  number={data.number}
  heading={templateTitle.lineage()}
  bind:summary
  {blocker}
  failure={failure == null
    ? null
    : {
        label:
          failure.row == null ? null : m.ws_finding_row({ row: failure.row }),
        text: failure.text,
        pick: failure.row == null ? null : () => pickRow(failure.row ?? 1),
      }}
>
  {#snippet outline()}
    <div class="grid gap-2 px-2 pbe-3">
      <p class="px-2 font-semibold text-pretty">
        {focus.entity?.name ?? templateTitle.lineage()}
      </p>
      <ul class="grid">
        {#each assessed as item (item.row.key)}
          {#if !item.isBlank}
            <li>
              <button
                type="button"
                class={cn(
                  "grid grid-cols-[minmax(0,1fr)] gap-0.5 rounded-sm px-2 py-1.5 text-start inline-full hover:bg-muted",
                  { "bg-muted": selected === item.row.key },
                )}
                onclick={() => pickRow(item.n)}
              >
                <span class="flex items-baseline justify-between gap-2">
                  <span class="truncate font-semibold min-inline-0"
                    >{named(item.looked.party, item.row.party)}</span
                  >
                  {#if item.problem != null || failure?.row === item.n}
                    <span class="font-figures font-semibold text-destructive"
                      >{item.n}</span
                    >
                  {/if}
                </span>
                <span class="truncate text-xs text-muted-foreground"
                  >{relationLabel[item.kind]()}{item.isClosure
                    ? ""
                    : ` ${named(item.looked.other, item.row.other)}`}</span
                >
              </button>
            </li>
          {/if}
        {/each}
        {#each (current ?? []).filter( (item) => drops.includes(item.id) ) as item (item.id)}
          <li class="px-2 py-1.5 text-xs text-muted-foreground">
            {m.ws_outline_removing({
              link: [
                item.from_name ?? item.from,
                relationLabel[item.kind](),
                item.to_name ?? "",
              ]
                .join(" ")
                .trim(),
            })}
          </li>
        {/each}
      </ul>
    </div>
  {/snippet}

  {#snippet editor()}
    <section class="grid gap-3 p-4" aria-labelledby="ws-lineage-current">
      <div class="sm:inline-1/2">
        <Ref
          bind:value={party}
          id="ws-lineage-party"
          label={m.ws_lineage_party()}
          {isLocked}
          state={focus}
        />
      </div>
      <h2 id="ws-lineage-current" class="mbs-2 font-semibold">
        {m.ws_lineage_current()}
      </h2>
      {#if focusId == null}
        <p class="text-pretty text-muted-foreground">
          {m.ws_lineage_current_hint()}
        </p>
      {:else if current == null}
        <p class="text-muted-foreground">{m.ws_record_looking()}</p>
      {:else if current.length === 0}
        <p class="text-pretty text-muted-foreground">
          {m.ws_lineage_current_empty()}
        </p>
      {:else}
        <ol class="grid border-bs">
          {#each current as item (item.id)}
            {const isDropped = $derived(drops.includes(item.id))}
            {const date = $derived(when(item.date, item.precision))}
            <li
              class="grid grid-cols-[5.5rem_minmax(0,1fr)_auto] items-baseline gap-x-3 border-be py-1.5"
            >
              <span class="font-figures text-sm text-muted-foreground"
                >{date}</span
              >
              <p
                class={cn("text-pretty", {
                  "text-muted-foreground": isDropped,
                })}
              >
                <span class={cn({ "line-through": isDropped })}>
                  <a
                    class={cn(
                      "underline decoration-border underline-offset-4",
                      { "font-semibold": item.from === focusId },
                    )}
                    href={resolve("/id/[id]", { id: item.from })}
                    target="_blank"
                    rel="noopener">{item.from_name ?? item.from}</a
                  >
                  <span class="text-muted-foreground"
                    >{relationLabel[item.kind]()}</span
                  >
                  {#if item.to != null}
                    <a
                      class={cn(
                        "underline decoration-border underline-offset-4",
                        { "font-semibold": item.to === focusId },
                      )}
                      href={resolve("/id/[id]", { id: item.to })}
                      target="_blank"
                      rel="noopener">{item.to_name ?? item.to}</a
                    >
                  {/if}
                  {#if item.election_name != null}
                    <span class="text-muted-foreground"
                      >({item.election_name})</span
                    >
                  {/if}
                </span>
                {#if isDropped}
                  <span class="block text-xs font-semibold text-foreground"
                    >{m.ws_lineage_removing()}</span
                  >
                {/if}
              </p>
              {#if !isLocked}
                <Button
                  variant="ghost"
                  size="sm"
                  class="px-2 text-xs"
                  onclick={() => toggle(item.id)}
                  >{isDropped
                    ? m.ws_lineage_keep()
                    : m.ws_lineage_remove()}</Button
                >
              {/if}
            </li>
          {/each}
        </ol>
      {/if}
    </section>

    <section class="grid gap-3 border-bs p-4" aria-labelledby="ws-lineage-new">
      <div class="flex flex-wrap items-baseline justify-between gap-x-4">
        <h2 id="ws-lineage-new" class="font-semibold">
          {m.ws_lineage_new()}
        </h2>
        <span class="text-xs text-pretty text-muted-foreground"
          >{m.ws_lineage_new_hint()}</span
        >
      </div>
      <ol class="grid border-bs">
        {#each assessed as item, index (item.row.key)}
          {const row = $derived(item.row)}
          {const label = $derived(m.ws_row_label({ row: index + 1 }))}
          {const isFailed = $derived(failure?.row === item.n)}
          <li
            class={cn(
              "grid grid-cols-[2rem_minmax(0,1fr)_2rem] gap-x-2 border-s-2 border-be py-2",
              {
                "border-s-foreground":
                  item.op != null && item.problem == null && !isFailed,
                "border-s-destructive": item.problem != null || isFailed,
                "border-s-transparent":
                  item.op == null && item.problem == null && !isFailed,
              },
              { "bg-muted": selected === row.key },
            )}
          >
            <span
              class="pbs-6 text-end font-figures text-xs text-muted-foreground"
              aria-hidden="true">{index + 1}</span
            >
            <div
              class="grid gap-x-3 gap-y-2 sm:grid-cols-[minmax(0,1fr)_11rem_minmax(0,1fr)_8rem]"
            >
              <Ref
                bind:value={row.party}
                id="ws-link-party-{row.key}"
                label="{label}, {m.ws_col_party()}"
                placeholder={focus.entity?.name ?? m.ws_record_placeholder()}
                {isLocked}
                state={{ ...item.looked.party, problem: item.problems.party }}
                onfocus={() => (selected = row.key)}
              />
              <div class="grid content-start gap-1 min-inline-0">
                <span
                  id="ws-link-kind-{row.key}-label"
                  class="text-xs text-muted-foreground"
                  >{label}, {m.ws_col_link()}</span
                >
                <Select.Root
                  type="single"
                  value={item.kind}
                  disabled={isLocked}
                  onValueChange={(chosen: string) => {
                    row.kind = chosen;
                  }}
                >
                  <Select.Trigger
                    id="ws-link-kind-{row.key}"
                    class="text-sm block-8 inline-full"
                    aria-labelledby="ws-link-kind-{row.key}-label"
                    >{relationLabel[item.kind]()}</Select.Trigger
                  >
                  <Select.Content>
                    {#each relationKinds as kind (kind)}
                      <Select.Item value={kind}
                        >{relationLabel[kind]()}</Select.Item
                      >
                    {/each}
                  </Select.Content>
                </Select.Root>
              </div>
              {#if !item.isClosure || row.other !== ""}
                <Ref
                  bind:value={row.other}
                  id="ws-link-other-{row.key}"
                  label="{label}, {m.ws_col_other()}"
                  {isLocked}
                  state={{ ...item.looked.other, problem: item.problems.other }}
                  onfocus={() => (selected = row.key)}
                />
              {:else}
                <span aria-hidden="true"></span>
              {/if}
              <div class="grid content-start gap-1">
                <label
                  for="ws-link-date-{row.key}"
                  class="text-xs text-muted-foreground"
                  >{label}, {m.ws_col_date()}</label
                >
                <Input
                  id="ws-link-date-{row.key}"
                  bind:value={row.date}
                  disabled={isLocked}
                  placeholder="YYYY-MM-DD"
                  title={m.ws_lineage_date_placeholder()}
                  inputmode="numeric"
                  autocomplete="off"
                  maxlength={10}
                  aria-invalid={item.problems.date != null}
                  class="font-figures block-8"
                  onfocus={() => (selected = row.key)}
                />
                {#if item.problems.date != null}
                  <p class="text-xs font-semibold text-pretty text-destructive">
                    {item.problems.date}
                  </p>
                {/if}
              </div>
              {#if item.isVehicle}
                <div class="sm:col-span-2">
                  <Ref
                    bind:value={row.election}
                    id="ws-link-election-{row.key}"
                    label="{label}, {m.ws_col_election()}"
                    {isLocked}
                    state={{
                      ...item.looked.election,
                      problem: item.problems.election,
                    }}
                  />
                </div>
              {/if}
              {#if isFailed}
                <p class="text-xs font-semibold text-destructive sm:col-span-4">
                  {failure?.text}
                </p>
              {/if}
            </div>
            <Button
              variant="ghost"
              size="icon"
              class="mbs-5 text-muted-foreground block-8 inline-8"
              disabled={isLocked}
              aria-label={m.ws_remove_row({ row: index + 1 })}
              onclick={() => removeRow(row.key)}><XIcon /></Button
            >
          </li>
        {/each}
      </ol>
      {#if !isLocked}
        <Button
          variant="outline"
          size="sm"
          class="justify-self-start"
          onclick={addRow}><PlusIcon />{m.ws_add_row()}</Button
        >
      {/if}
    </section>
  {/snippet}

  {#snippet diff()}
    {#if changed.length === 0}
      <p class="text-pretty text-muted-foreground">{m.ws_diff_empty()}</p>
    {:else}
      {#if session.isStale}
        <p class="pbe-2 text-xs font-semibold">{m.ws_checks_stale()}</p>
      {/if}
      <dl class="grid border-s-2 border-foreground">
        {#each changed as entry (entry.table)}
          <div
            class="flex items-baseline justify-between border-be py-1.5 ps-3"
          >
            <dt>{entry.label()}</dt>
            <dd class="font-figures font-semibold">{entry.count}</dd>
          </div>
        {/each}
      </dl>
    {/if}
  {/snippet}

  {#snippet evidence()}
    <Cite bind:cited {isLocked} empty={m.ws_lineage_evidence_needed()} />
  {/snippet}

  {#snippet commands(act: (action: () => void) => void)}
    <Command.Item disabled={isLocked} onSelect={() => act(addRow)}
      >{m.ws_add_row()}</Command.Item
    >
  {/snippet}
</Shell>
