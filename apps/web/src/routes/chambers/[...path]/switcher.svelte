<script lang="ts">
  import { resolve } from "$app/paths";
  import CheckIcon from "@lucide/svelte/icons/check";
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import * as DropdownMenu from "#lib/components/ui/dropdown-menu/index.js";
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    siblings,
    current,
  }: {
    siblings: Array<{
      id: string;
      name: string;
      path: string;
      kind: string;
      from: string | null;
      to: string | null;
      of: { id: string } | null;
    }>;
    current: string;
  } = $props();

  const KINDS: Record<string, () => string> = {
    lower_house: m.chamber_kind_lower_house,
    upper_house: m.chamber_kind_upper_house,
    unicameral: m.chamber_kind_unicameral,
    council: m.chamber_kind_council,
    constituent_assembly: m.chamber_kind_constituent_assembly,
    appointed_assembly: m.chamber_kind_appointed_assembly,
  };
  const RANK: Record<string, number> = {
    lower_house: 0,
    unicameral: 0,
    upper_house: 1,
    council: 2,
  };
  const today = new Date().toISOString().slice(0, 10);

  const sitting = $derived.by(() => {
    const open = siblings.filter((sibling) => sibling.to == null);
    if (open.length > 0) {
      return open;
    }
    let last = "";
    for (const sibling of siblings) {
      last = (sibling.to ?? "") > last ? (sibling.to ?? "") : last;
    }
    return siblings.filter((sibling) => sibling.to === last);
  });
  const leading = $derived(
    sitting.toSorted(
      (a, b) =>
        (RANK[a.kind] ?? 3) - (RANK[b.kind] ?? 3) ||
        (a.from ?? "").localeCompare(b.from ?? ""),
    ),
  );
  const former = $derived(
    siblings
      .filter((sibling) => !sitting.includes(sibling))
      .toSorted(
        (a, b) =>
          (a.from ?? "9999").localeCompare(b.from ?? "9999") ||
          (a.to ?? "9999").localeCompare(b.to ?? "9999") ||
          a.name.localeCompare(b.name),
      ),
  );
  const groups = $derived(
    [
      {
        heading: m.chamber_nested(),
        rows: former.filter((sibling) => sibling.of != null),
      },
      {
        heading: m.chamber_former(),
        rows: former.filter((sibling) => sibling.of == null),
      },
    ].filter((group) => group.rows.length > 0),
  );
  const isFormer = $derived(former.some((sibling) => sibling.id === current));
  const axis = $derived.by(() => {
    let low = today;
    for (const sibling of siblings) {
      if (sibling.from != null && sibling.from < low) {
        low = sibling.from;
      }
    }
    return { low: Date.parse(low), high: Date.parse(today) };
  });
  const place = (from: string | null, to: string | null) => {
    const width = Math.max(1, axis.high - axis.low);
    const start = Date.parse(from ?? today) - axis.low;
    const end = Date.parse(to ?? today) - axis.low;
    return {
      start: `${(start / width) * 100}%`,
      size: `${(Math.max(0, end - start) / width) * 100}%`,
    };
  };
  const years = (from: string | null, to: string | null) => {
    const first = from?.slice(0, 4) ?? "";
    const last = to?.slice(0, 4) ?? "";
    return to != null && first === last
      ? first
      : m.polity_span({
          first,
          last: to == null ? m.governments_present() : last,
        });
  };
</script>

<nav
  class="flex items-stretch gap-2 max-sm:inline-full"
  aria-label={m.record_tabs()}
>
  <div class="flex min-inline-0 max-sm:flex-1">
    {#each leading as sibling (sibling.id)}
      <a
        class={cn(
          "-ms-px flex items-center justify-center border border-input bg-card px-2.5 py-1 text-center text-[0.8125rem]/tight font-semibold text-balance keycap min-block-8 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary max-sm:flex-1",
          {
            "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
              sibling.id === current,
          },
        )}
        aria-current={sibling.id === current ? "page" : undefined}
        href={resolve("/[...path]", { path: sibling.path })}>{sibling.name}</a
      >
    {/each}
  </div>
  {#if former.length > 0}
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        class={cn(
          "flex shrink-0 items-center gap-1.5 rounded-sm border border-input bg-card ps-2.5 pe-2 text-[0.8125rem]/tight font-semibold keycap min-block-8 hover:bg-secondary data-[state=open]:bg-secondary",
          {
            "border-primary bg-primary text-primary-foreground hover:bg-primary data-[state=open]:bg-primary":
              isFormer,
          },
        )}
        aria-label={m.chamber_former_count({ count: count(former.length) })}
      >
        <span>{m.chamber_former()}</span>
        <span
          class={cn("font-figures text-muted-foreground tabular-nums", {
            "text-primary-foreground/70": isFormer,
          })}>{count(former.length)}</span
        >
        <ChevronDownIcon
          class="opacity-60 block-3.5 inline-3.5"
          aria-hidden="true"
        />
      </DropdownMenu.Trigger>
      <DropdownMenu.Content
        align="end"
        class="inline-[min(23rem,calc(100vw-1.5rem))]"
      >
        {#each groups as group, index (group.heading)}
          {#if index > 0}<DropdownMenu.Separator />{/if}
          <DropdownMenu.Group>
            <DropdownMenu.GroupHeading
              class="grid grid-cols-[minmax(0,1fr)_5.5rem] items-baseline gap-4"
            >
              <span>{group.heading}</span>
              <span
                class="flex justify-between font-figures font-normal tabular-nums"
                aria-hidden="true"
                ><span>{new Date(axis.low).getUTCFullYear()}</span><span
                  >{today.slice(0, 4)}</span
                ></span
              >
            </DropdownMenu.GroupHeading>
            {#each group.rows as sibling (sibling.id)}
              {const at = $derived(place(sibling.from, sibling.to))}
              <DropdownMenu.Item
                class="grid grid-cols-[minmax(0,1fr)_5.5rem] items-center gap-4 py-1.5"
              >
                {#snippet child({ props })}
                  <a
                    {...props}
                    href={resolve("/[...path]", { path: sibling.path })}
                    aria-current={sibling.id === current ? "page" : undefined}
                  >
                    <span class="grid gap-0.5 min-inline-0">
                      <span
                        class="flex items-center gap-1.5 text-[0.8125rem]/tight font-semibold"
                        ><span class="min-inline-0">{sibling.name}</span
                        >{#if sibling.id === current}<CheckIcon
                            class="shrink-0 block-3.5 inline-3.5"
                            aria-hidden="true"
                          />{/if}</span
                      >
                      <span
                        class="font-figures text-xs text-muted-foreground tabular-nums"
                        >{sibling.from == null
                          ? (KINDS[sibling.kind] ?? m.chamber_kind_other)()
                          : m.chamber_lifespan({
                              kind: (
                                KINDS[sibling.kind] ?? m.chamber_kind_other
                              )(),
                              years: years(sibling.from, sibling.to),
                            })}</span
                      >
                    </span>
                    <span
                      class="relative overflow-clip rounded-full bg-border block-1.5"
                      aria-hidden="true"
                    >
                      <span
                        class="absolute inset-y-0 rounded-full bg-foreground min-inline-0.75"
                        style:inset-inline-start={at.start}
                        style:inline-size={at.size}
                      ></span>
                    </span>
                  </a>
                {/snippet}
              </DropdownMenu.Item>
            {/each}
          </DropdownMenu.Group>
        {/each}
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  {/if}
</nav>
