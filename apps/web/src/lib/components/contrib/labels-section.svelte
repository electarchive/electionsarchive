<script lang="ts">
  import PlusIcon from "@lucide/svelte/icons/plus";
  import { SvelteSet } from "svelte/reactivity";
  import type { Change, Editable, LabelKind } from "#lib/contrib.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import * as Select from "#lib/components/ui/select/index.js";
  import {
    assessed,
    ballotValues,
    isDay,
    labelKindLabel,
    labelValue,
    period,
  } from "#lib/contrib.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  let {
    labels,
    signedIn,
    changes = $bindable([]),
    invalid = $bindable(false),
  }: {
    labels: Editable["labels"];
    signedIn: boolean;
    changes?: Array<Change>;
    invalid?: boolean;
  } = $props();

  const drops = new SvelteSet<string>();
  let draft = $state<{
    kind: LabelKind;
    value: string;
    assessor: string;
    from: string;
    to: string;
  } | null>(null);

  const problem = $derived.by(() => {
    if (draft == null) {
      return null;
    }
    const value = draft.value.trim();
    if (value === "") {
      return m.edit_label_needs_value();
    }
    if (assessed.has(draft.kind) && draft.assessor.trim() === "") {
      return m.edit_label_needs_assessor();
    }
    const from = draft.from.trim();
    const to = draft.to.trim();
    if ((from !== "" && !isDay(from)) || (to !== "" && !isDay(to))) {
      return m.edit_date_invalid();
    }
    if (from !== "" && to !== "" && to <= from) {
      return m.edit_color_order();
    }
    const kind = draft.kind;
    if (
      labels.held.some(
        (label) =>
          label.kind === kind &&
          label.value === value &&
          label.valid_to == null &&
          !drops.has(label.id),
      )
    ) {
      return m.edit_label_held();
    }
    return null;
  });

  const proposed = $derived.by(() => {
    const list: Array<Change> = labels.held
      .filter((label) => drops.has(label.id))
      .map((label) => ({ kind: "label_drop", id: label.id }));
    if (draft != null && problem == null) {
      const assessor = draft.assessor.trim();
      const from = draft.from.trim();
      const to = draft.to.trim();
      list.push({
        kind: "label",
        label: draft.kind,
        value: draft.value.trim(),
        assessor: assessor === "" ? null : assessor,
        from: from === "" ? null : from,
        to: to === "" ? null : to,
      });
    }
    return list;
  });

  $effect(() => {
    changes = proposed;
    invalid = draft != null && problem != null;
  });

  const start = () => {
    const kind = labels.kinds[0];
    if (kind != null) {
      draft = {
        kind,
        value: kind === "ballot" ? ballotValues[0] : "",
        assessor: "",
        from: "",
        to: "",
      };
    }
  };
</script>

<section class="border-be px-4 py-3" aria-labelledby="edit-labels">
  <h3 id="edit-labels" class="text-sm font-bold">{m.edit_labels()}</h3>
  {#if labels.held.length === 0 && draft == null}
    <p class="mbs-1 text-sm text-muted-foreground">{m.edit_labels_empty()}</p>
  {/if}
  <dl
    class="mbs-2 grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm"
  >
    {#each labels.held as label (label.id)}
      {const dropped = $derived(drops.has(label.id))}
      <dt class="pbs-1.5 text-muted-foreground">
        {labelKindLabel[label.kind]()}
      </dt>
      <dd class="flex items-start justify-between gap-2 py-1">
        <span
          class={cn("grid px-1.5", {
            "contrib-settle text-muted-foreground line-through decoration-foreground/50":
              dropped,
          })}
        >
          <span>{labelValue(label.value)}</span>
          <span class="text-xs text-muted-foreground"
            >{[
              label.assessor == null
                ? null
                : m.edit_label_by({ assessor: label.assessor }),
              period(label.valid_from, "day", label.valid_to, "day"),
              label.source,
            ]
              .filter((part) => part != null)
              .join(" · ")}</span
          >
        </span>
        {#if signedIn}
          <span class="flex shrink-0 items-center gap-2">
            {#if dropped}
              <span class="text-xs font-semibold">{m.edit_removed()}</span>
            {/if}
            <Button
              variant="ghost"
              size="sm"
              class="px-2 text-xs text-muted-foreground block-7"
              onclick={() => {
                if (dropped) {
                  drops.delete(label.id);
                } else {
                  drops.add(label.id);
                }
              }}>{dropped ? m.edit_label_keep() : m.edit_label_remove()}</Button
            >
          </span>
        {/if}
      </dd>
    {/each}
    {#if draft != null}
      <dt class="pbs-2 font-semibold text-foreground">{m.edit_label_add()}</dt>
      <dd
        class="contrib-settle grid gap-1.5 border-s-2 border-foreground py-1 ps-2"
      >
        <Select.Root
          type="single"
          value={draft.kind}
          onValueChange={(next: string) => {
            const found = labels.kinds.find((kind) => kind === next);
            if (found != null && draft != null) {
              draft.kind = found;
              draft.value = found === "ballot" ? ballotValues[0] : "";
            }
          }}
        >
          <Select.Trigger
            class="text-sm block-8"
            aria-label={m.edit_label_kind()}
            >{labelKindLabel[draft.kind]()}</Select.Trigger
          >
          <Select.Content>
            {#each labels.kinds as kind (kind)}
              <Select.Item value={kind}>{labelKindLabel[kind]()}</Select.Item>
            {/each}
          </Select.Content>
        </Select.Root>
        {#if draft.kind === "ballot"}
          <Select.Root
            type="single"
            value={draft.value}
            onValueChange={(next: string) => {
              if (draft != null) {
                draft.value = next;
              }
            }}
          >
            <Select.Trigger
              class="text-sm block-8"
              aria-label={m.edit_label_value()}
              >{labelValue(draft.value)}</Select.Trigger
            >
            <Select.Content>
              {#each ballotValues as value (value)}
                <Select.Item {value}>{labelValue(value)}</Select.Item>
              {/each}
            </Select.Content>
          </Select.Root>
        {:else}
          <Input
            bind:value={draft.value}
            placeholder={m.edit_label_value()}
            aria-label={m.edit_label_value()}
            maxlength={200}
            class="text-sm block-8"
          />
        {/if}
        {#if assessed.has(draft.kind)}
          <Input
            bind:value={draft.assessor}
            placeholder={m.edit_label_assessor()}
            aria-label={m.edit_label_assessor()}
            maxlength={200}
            class="text-sm block-8"
          />
          <p class="text-xs text-pretty text-muted-foreground">
            {m.edit_label_assessor_hint()}
          </p>
        {/if}
        <div class="grid grid-cols-2 gap-2">
          <label class="grid gap-0.5 text-xs text-muted-foreground">
            {m.edit_color_from()}
            <Input
              bind:value={draft.from}
              placeholder="YYYY-MM-DD"
              inputmode="numeric"
              autocomplete="off"
              maxlength={10}
              class="font-figures text-sm block-8"
            />
          </label>
          <label class="grid gap-0.5 text-xs text-muted-foreground">
            {m.edit_color_to()}
            <Input
              bind:value={draft.to}
              placeholder="YYYY-MM-DD"
              inputmode="numeric"
              autocomplete="off"
              maxlength={10}
              class="font-figures text-sm block-8"
            />
          </label>
        </div>
        <div
          class="flex items-center justify-between gap-2 text-xs text-muted-foreground"
        >
          <span class={cn({ "text-destructive": problem != null })}
            >{problem ?? m.edit_color_dates()}</span
          >
          <Button
            variant="ghost"
            size="sm"
            class="px-2 text-xs block-7"
            onclick={() => (draft = null)}>{m.edit_label_discard()}</Button
          >
        </div>
      </dd>
    {/if}
  </dl>
  {#if signedIn && draft == null && labels.kinds.length > 0}
    <Button variant="outline" size="sm" class="mbs-2" onclick={start}
      ><PlusIcon />{m.edit_label_add()}</Button
    >
  {/if}
</section>
