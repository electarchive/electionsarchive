<script lang="ts">
  import { untrack } from "svelte";
  import type { Box } from "#lib/map/engine.svelte.js";
  import type { Quality, Shaped } from "#lib/workspace.js";
  import BoundaryKey from "#lib/components/contrib/boundary-key.svelte";
  import UploadEvidence from "#lib/components/contrib/upload-evidence.svelte";
  import { Combobox } from "#lib/components/ui/combobox/index.js";
  import * as Command from "#lib/components/ui/command/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import * as Select from "#lib/components/ui/select/index.js";
  import { extent, isDay, sourceKindLabel, sourceKinds } from "#lib/contrib.js";
  import { count } from "#lib/format.js";
  import { ADDED, CURRENT, KEPT, sketch } from "#lib/map/boundaries.js";
  import { Engine } from "#lib/map/engine.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import {
    languageItems,
    languageLabel,
    qualities,
    qualityLabel,
    templateTitle,
  } from "#lib/workspace.js";
  import type { PageData } from "./$types";
  import { Session } from "./session.svelte.js";
  import Shell from "./shell.svelte";

  const {
    data,
    workspace,
  }: {
    data: PageData;
    workspace: Extract<PageData["workspace"], { template: "boundaries" }>;
  } = $props();

  type Status = "matched" | "new" | "unmatched" | "blank" | "excluded";

  const WORLD: Box = { west: -170, south: -58, east: 170, north: 75 };
  const NONE = "\u0000";

  const initial = untrack(() => ({ ...data, workspace }));
  let scheme = $state(initial.workspace.scheme);
  let field = $state(initial.workspace.field);
  let nameField = $state(initial.workspace.nameField);
  let lang = $state<string | null>(initial.workspace.lang);
  let from = $state(initial.workspace.from);
  let to = $state(initial.workspace.to);
  let quality = $state<Quality>(initial.workspace.quality);
  let exclude = $state<Array<string>>(initial.workspace.exclude);
  const cited = $state(
    initial.workspace.evidence ?? {
      upload: initial.upload?.id ?? null,
      title: "",
      publisher: "",
      kind: "official_body" as const,
    },
  );
  let summary = $state(initial.summary);
  let shaped = $state.raw<Shaped | null>(null);
  let isReading = $state(false);
  let readError = $state<string | null>(null);
  let focus = $state<string | null>(null);
  let hover = $state<{ x: number; y: number; id: number } | null>(null);
  let framed: string | null = null;

  const languages = languageItems();
  const schemeItems = $derived(
    initial.schemes.map((item) => ({
      value: item.code,
      label: item.code,
      detail: item.noun,
    })),
  );
  const today = new Date().toISOString().slice(0, 10);
  const on = $derived(isDay(from) ? from : today);
  const fromProblem = $derived(
    from.trim() === "" ? null : isDay(from.trim()) ? null : m.ws_bd_bad_date(),
  );
  const toProblem = $derived(
    to.trim() === ""
      ? null
      : !isDay(to.trim())
        ? m.ws_bd_bad_date()
        : isDay(from.trim()) && to.trim() <= from.trim()
          ? m.ws_bd_to_before()
          : null,
  );

  $effect(() => {
    const upload = cited.upload;
    if (upload == null) {
      shaped = null;
      return;
    }
    const body = JSON.stringify({ upload, scheme, field, name: nameField, on });
    const controller = new AbortController();
    const timer = setTimeout(() => {
      isReading = true;
      void (async () => {
        try {
          const response = await fetch("/edit/shapes", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body,
            signal: controller.signal,
          });
          if (!response.ok) {
            const reply = (await response.json()) as { message?: string };
            readError = reply.message ?? response.statusText;
            return;
          }
          shaped = (await response.json()) as Shaped;
          readError = null;
        } catch (error_) {
          if (!controller.signal.aborted) {
            readError =
              error_ instanceof Error ? error_.message : String(error_);
          }
        } finally {
          if (!controller.signal.aborted) {
            isReading = false;
          }
        }
      })();
    }, 200);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  });

  const fields = $derived(shaped?.fields ?? []);
  const features = $derived(shaped?.map?.features ?? []);
  const byId = $derived(
    new Map(features.map((feature) => [feature.id, feature])),
  );
  const excluded = $derived(new Set(exclude));

  const statusOf = (feature: {
    code: string | null;
    area: string | null;
  }): Status =>
    feature.code == null
      ? "blank"
      : excluded.has(feature.code)
        ? "excluded"
        : feature.area != null
          ? "matched"
          : nameField === ""
            ? "unmatched"
            : "new";

  const codes = $derived.by(() => {
    if (field === "") {
      return [];
    }
    const grouped: Record<
      string,
      { code: string | null; name: string; status: Status; box: Box | null }
    > = {};
    for (const feature of features) {
      const key = feature.code ?? NONE;
      const known = grouped[key];
      if (known == null) {
        grouped[key] = {
          code: feature.code,
          name: feature.name ?? feature.label ?? "",
          status: statusOf(feature),
          box: extent(feature.geometry),
        };
      } else {
        known.box = extent(feature.geometry, known.box);
      }
    }
    const order: Record<Status, number> = {
      blank: 0,
      unmatched: 1,
      new: 2,
      matched: 3,
      excluded: 4,
    };
    return Object.values(grouped).toSorted(
      (a, b) =>
        order[a.status] - order[b.status] ||
        (a.code ?? "").localeCompare(b.code ?? "", undefined, {
          numeric: true,
        }),
    );
  });

  const tally = $derived.by(() => {
    const counts: Record<Status, number> = {
      matched: 0,
      new: 0,
      unmatched: 0,
      blank: 0,
      excluded: 0,
    };
    for (const item of codes) {
      counts[item.status] += 1;
    }
    return counts;
  });

  const unnamed = $derived(
    codes.filter((item) => item.status === "new" && item.name === "").length,
  );

  const problems = $derived(
    [
      tally.blank > 0 ? m.ws_bd_problem_blank({ field }) : null,
      tally.unmatched > 0
        ? m.ws_bd_problem_unmatched({ count: tally.unmatched })
        : null,
      unnamed > 0 ? m.ws_bd_problem_unnamed({ count: unnamed }) : null,
      nameField !== "" && lang == null && tally.new > 0
        ? m.ws_bd_problem_lang()
        : null,
      codes.length > 0 && tally.excluded === codes.length
        ? m.ws_bd_problem_all_excluded()
        : null,
    ].filter((item) => item != null),
  );

  const source = $derived.by(() => {
    const title = cited.title.trim();
    const publisher = cited.publisher.trim();
    return cited.upload != null && title !== "" && publisher !== ""
      ? { upload: cited.upload, title, publisher, kind: cited.kind }
      : null;
  });

  const isSet = $derived(
    scheme !== "" &&
      field !== "" &&
      isDay(from.trim()) &&
      fromProblem == null &&
      toProblem == null &&
      cited.upload != null,
  );

  const ops = $derived(
    isSet
      ? [
          {
            op: "shape" as const,
            scheme,
            field,
            ...(nameField !== "" && {
              name_field: nameField,
              ...(lang != null && { lang }),
            }),
            valid_from: from.trim(),
            ...(to.trim() !== "" && { valid_to: to.trim() }),
            quality,
            ...(exclude.length > 0 && { exclude }),
            evidence: ["e1"],
          },
        ]
      : [],
  );

  const compiled = $derived(JSON.stringify([ops, source]));

  const serialized = $derived(
    JSON.stringify({
      summary: summary.trim(),
      ops,
      ...(source != null && { evidence: { e1: source } }),
      workspace: {
        template: "boundaries",
        scheme,
        field,
        nameField,
        lang,
        from: from.trim(),
        to: to.trim(),
        quality,
        exclude,
        evidence: cited,
      },
    }),
  );

  const session = new Session(
    untrack(() => data.number),
    untrack(() => data.check),
    {
      serialized: () => serialized,
      compiled: () => compiled,
      canCheck: () =>
        ops.length > 0 &&
        source != null &&
        problems.length === 0 &&
        !isReading &&
        shaped?.map != null,
    },
  );

  const check = $derived(session.check);
  const isLocked = $derived(session.isLocked);
  const drawn = $derived(
    check?.status === "done" ? (check.diff?.map ?? null) : null,
  );

  const failure = $derived(
    session.error == null
      ? null
      : { label: null, text: session.error.text, pick: null },
  );

  const blocker = $derived(
    session.blocker(
      problems.length > 0 || failure != null
        ? m.ws_submit_needs_fix()
        : cited.upload == null
          ? m.ws_bd_needs_file()
          : scheme === ""
            ? m.ws_bd_needs_scheme()
            : field === ""
              ? m.ws_bd_needs_field()
              : !isDay(from.trim())
                ? m.ws_bd_needs_from()
                : source == null
                  ? m.ws_bd_needs_source()
                  : null,
      summary,
    ),
  );

  const changed = $derived(
    (check?.diff?.rows ?? []).filter((row) => row.table === "area_geometry"),
  );
  const labels = $derived(
    new Map(
      (drawn?.features ?? [])
        .filter((feature) => feature.properties.kind === "after")
        .map((feature) => [
          feature.properties.area ?? "",
          {
            code: feature.properties.code ?? "",
            name: feature.properties.label ?? "",
          },
        ]),
    ),
  );
  const added = $derived(check?.checks?.diff?.area?.insert ?? 0);

  const engine = new Engine(
    WORLD,
    (width, height) => {
      const edge = Math.max(16, Math.round(Math.min(width, height) * 0.05));
      return { top: edge, right: edge, bottom: edge, left: edge };
    },
    {
      onReady: () => untrack(draw),
      onPointer: ({ kind, point, isTouch }) => {
        if (kind === "leave") {
          hover = null;
          engine.cursor(false);
          return;
        }
        const id = engine.pick(point, ["bd-fill"]);
        if (kind === "click") {
          const feature = id == null ? null : byId.get(id);
          if (feature?.code != null && !isLocked) {
            toggle(feature.code);
          }
          return;
        }
        if (isTouch) {
          return;
        }
        hover = id == null ? null : { ...point, id };
        engine.cursor(id != null && !isLocked);
      },
    },
  );

  const collection = $derived({
    type: "FeatureCollection" as const,
    features: features.flatMap((feature) =>
      feature.geometry == null
        ? []
        : [
            {
              type: "Feature" as const,
              id: feature.id,
              properties: {
                code: feature.code ?? "",
                status: field === "" ? "pending" : statusOf(feature),
              },
              geometry: feature.geometry,
            },
          ],
    ),
  });

  $effect(() => {
    void collection;
    void drawn;
    void focus;
    if (engine.gl != null) {
      draw();
    }
  });

  function draw() {
    const gl = engine.gl;
    const map = shaped?.map;
    if (gl == null) {
      return;
    }
    const tiles = map?.tiles ?? null;
    if (tiles != null && !engine.has(`bd-current:${tiles}`)) {
      engine.drop("bd-current");
      const current = engine.tiles(tiles, "bd-current");
      engine.add(
        {
          id: `bd-current:${tiles}`,
          type: "line",
          source: current,
          "source-layer": "areas",
          paint: { "line-color": CURRENT, "line-width": 0.75 },
        },
        "etch",
        { group: "bd-current" },
      );
    } else if (tiles == null) {
      engine.drop("bd-current");
    }
    const upload = engine.shapes("bd-upload", collection, "bd");
    if (!engine.has("bd-fill")) {
      engine.add(
        {
          id: "bd-fill",
          type: "fill",
          source: upload,
          paint: {
            "fill-color": [
              "match",
              ["get", "status"],
              "new",
              ADDED,
              "unmatched",
              "color-mix(in oklab, var(--destructive) 22%, transparent)",
              "blank",
              "color-mix(in oklab, var(--destructive) 22%, transparent)",
              "excluded",
              "rgba(0, 0, 0, 0)",
              KEPT,
            ],
          },
        },
        "fills",
        { group: "bd", rank: 1 },
      );
      engine.add(
        {
          id: "bd-line",
          type: "line",
          source: upload,
          filter: ["!=", ["get", "status"], "excluded"],
          paint: { "line-color": "var(--foreground)", "line-width": 1.25 },
        },
        "lines",
        { group: "bd" },
      );
      engine.add(
        {
          id: "bd-excluded",
          type: "line",
          source: upload,
          filter: ["==", ["get", "status"], "excluded"],
          paint: {
            "line-color": "var(--muted-foreground)",
            "line-width": 1,
            "line-dasharray": [2, 2],
          },
        },
        "lines",
        { group: "bd" },
      );
      engine.add(
        {
          id: "bd-focus",
          type: "line",
          source: upload,
          filter: ["==", ["get", "code"], NONE],
          paint: { "line-color": "var(--foreground)", "line-width": 3 },
        },
        "focus",
        { group: "bd" },
      );
    }
    engine.filter("bd-focus", ["==", ["get", "code"], focus ?? NONE]);
    sketch(engine, drawn, "bd-drawn");
    const key = `${shaped?.id ?? ""}|${scheme}`;
    if (map != null && framed !== key) {
      framed = key;
      engine.frame(map.frame);
    }
  }

  function toggle(code: string) {
    exclude = excluded.has(code)
      ? exclude.filter((item) => item !== code)
      : [...exclude, code];
  }

  function show(code: string | null, box: Box | null) {
    focus = code;
    if (box != null) {
      engine.fit(box);
    }
  }

  function replace() {
    cited.upload = null;
    field = "";
    nameField = "";
    exclude = [];
  }

  const hovered = $derived(hover == null ? null : (byId.get(hover.id) ?? null));

  const statusLabel: Record<Status, () => string> = {
    matched: m.ws_bd_status_matched,
    new: m.ws_bd_status_new,
    unmatched: m.ws_bd_status_unmatched,
    blank: m.ws_bd_status_blank,
    excluded: m.ws_bd_status_excluded,
  };
</script>

<Shell
  {session}
  number={data.number}
  heading={templateTitle.boundaries()}
  bind:summary
  {blocker}
  {failure}
>
  {#snippet outline()}
    <div class="grid gap-2 px-2 pbe-3">
      <p class="px-2 font-semibold">
        {scheme === "" ? templateTitle.boundaries() : scheme}
      </p>
      {#if codes.length === 0}
        <p class="px-2 text-xs text-pretty text-muted-foreground">
          {m.ws_bd_outline_empty()}
        </p>
      {:else}
        <ul class="grid">
          {#each codes as item (item.code ?? NONE)}
            <li>
              <button
                type="button"
                class={cn(
                  "grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-2 rounded-sm px-2 py-1 text-start inline-full hover:bg-muted",
                  { "bg-muted": focus != null && focus === item.code },
                  { "text-muted-foreground": item.status === "excluded" },
                )}
                onclick={() => show(item.code, item.box)}
              >
                <span class="truncate min-inline-0">
                  <span class="font-figures font-semibold"
                    >{item.code ?? m.ws_bd_no_code()}</span
                  >
                  {#if item.name !== ""}<span class="ms-1.5">{item.name}</span
                    >{/if}
                </span>
                <span
                  class={cn("text-xs", {
                    "font-semibold text-destructive":
                      item.status === "unmatched" || item.status === "blank",
                    "font-semibold": item.status === "new",
                  })}>{statusLabel[item.status]()}</span
                >
              </button>
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  {/snippet}

  {#snippet editor()}
    <section class="grid gap-x-4 gap-y-3 p-4 sm:grid-cols-2 xl:grid-cols-4">
      <div class="grid content-start gap-1 sm:col-span-2">
        <span class="text-xs text-muted-foreground" aria-hidden="true"
          >{m.ws_bd_scheme()}</span
        >
        {#if isLocked}
          <p class="font-figures text-sm/8">{scheme}</p>
        {:else}
          <Combobox
            items={schemeItems}
            value={scheme === "" ? null : scheme}
            label={m.ws_bd_scheme()}
            placeholder={m.ws_bd_scheme_pick()}
            search={m.ws_bd_scheme_search()}
            empty={m.ws_bd_scheme_empty()}
            class="block-8 inline-full"
            onValueChange={(chosen: string | null) => {
              scheme = chosen ?? "";
              exclude = [];
            }}
          />
        {/if}
      </div>
      <div class="grid content-start gap-1">
        <label for="ws-bd-from" class="text-xs text-muted-foreground"
          >{m.ws_bd_from()}</label
        >
        <Input
          id="ws-bd-from"
          bind:value={from}
          disabled={isLocked}
          placeholder="YYYY-MM-DD"
          inputmode="numeric"
          autocomplete="off"
          maxlength={10}
          aria-invalid={fromProblem != null}
          class="font-figures block-8"
        />
        {#if fromProblem != null}
          <p class="text-xs font-semibold text-destructive">{fromProblem}</p>
        {/if}
      </div>
      <div class="grid content-start gap-1">
        <label for="ws-bd-to" class="text-xs text-muted-foreground"
          >{m.ws_bd_to()}</label
        >
        <Input
          id="ws-bd-to"
          bind:value={to}
          disabled={isLocked}
          placeholder={m.ws_bd_to_open()}
          inputmode="numeric"
          autocomplete="off"
          maxlength={10}
          aria-invalid={toProblem != null}
          class="font-figures block-8"
        />
        {#if toProblem != null}
          <p class="text-xs font-semibold text-destructive">{toProblem}</p>
        {/if}
      </div>
    </section>

    <section class="grid gap-3 border-bs p-4" aria-labelledby="ws-bd-file">
      <h2 id="ws-bd-file" class="font-semibold">{m.ws_bd_file()}</h2>
      {#if cited.upload == null}
        <p class="text-xs text-pretty text-muted-foreground">
          {m.ws_bd_file_hint()}
        </p>
        {#if !isLocked}
          <UploadEvidence
            bind:value={
              () => null,
              (next: { id: string } | null) => {
                if (next != null) {
                  cited.upload = next.id;
                }
              }
            }
          />
        {/if}
      {:else}
        <p class="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <span class="truncate font-semibold min-inline-0"
            >{shaped?.filename ?? initial.upload?.filename ?? ""}</span
          >
          {#if shaped != null}
            <span class="font-figures text-muted-foreground"
              >{m.ws_bd_shapes({ count: shaped.count })}</span
            >
          {/if}
          {#if !isLocked}
            <button
              type="button"
              class="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
              onclick={replace}>{m.upload_replace()}</button
            >
          {/if}
        </p>
        {#if readError != null}
          <p class="text-sm font-semibold text-pretty text-destructive">
            {readError}
          </p>
        {/if}
        <div class="grid gap-x-4 gap-y-3 sm:grid-cols-2 xl:grid-cols-4">
          <div class="grid content-start gap-1">
            <span id="ws-bd-field-label" class="text-xs text-muted-foreground"
              >{m.ws_bd_field()}</span
            >
            <Select.Root
              type="single"
              value={field}
              disabled={isLocked || fields.length === 0}
              onValueChange={(chosen: string) => {
                field = chosen;
                exclude = [];
              }}
            >
              <Select.Trigger
                class="text-sm block-8 inline-full"
                aria-labelledby="ws-bd-field-label"
                >{field === "" ? m.ws_bd_field_pick() : field}</Select.Trigger
              >
              <Select.Content>
                {#each fields as item (item.name)}
                  <Select.Item value={item.name} label={item.name}>
                    <span class="grid gap-0.5">
                      <span
                        >{item.name}{#if item.unique}<span
                            class="ms-2 text-xs text-muted-foreground"
                            >{m.ws_bd_field_unique()}</span
                          >{/if}</span
                      >
                      <span class="truncate text-xs text-muted-foreground"
                        >{item.samples.join(", ")}</span
                      >
                    </span>
                  </Select.Item>
                {/each}
              </Select.Content>
            </Select.Root>
          </div>
          <div class="grid content-start gap-1">
            <span id="ws-bd-name-label" class="text-xs text-muted-foreground"
              >{m.ws_bd_name_field()}</span
            >
            <Select.Root
              type="single"
              value={nameField === "" ? NONE : nameField}
              disabled={isLocked || fields.length === 0}
              onValueChange={(chosen: string) => {
                nameField = chosen === NONE ? "" : chosen;
              }}
            >
              <Select.Trigger
                class="text-sm block-8 inline-full"
                aria-labelledby="ws-bd-name-label"
                >{nameField === ""
                  ? m.ws_bd_name_none()
                  : nameField}</Select.Trigger
              >
              <Select.Content>
                <Select.Item value={NONE} label={m.ws_bd_name_none()}
                  >{m.ws_bd_name_none()}</Select.Item
                >
                {#each fields as item (item.name)}
                  <Select.Item value={item.name} label={item.name}>
                    <span class="grid gap-0.5">
                      <span>{item.name}</span>
                      <span class="truncate text-xs text-muted-foreground"
                        >{item.samples.join(", ")}</span
                      >
                    </span>
                  </Select.Item>
                {/each}
              </Select.Content>
            </Select.Root>
          </div>
          <div class="grid content-start gap-1">
            <span class="text-xs text-muted-foreground" aria-hidden="true"
              >{m.ws_bd_lang()}</span
            >
            {#if isLocked || nameField === ""}
              <p class="text-sm/8 text-muted-foreground">
                {lang == null ? m.ws_bd_lang_none() : languageLabel(lang)}
              </p>
            {:else}
              <Combobox
                items={languages}
                value={lang}
                label={m.ws_bd_lang()}
                placeholder={m.ws_bd_lang_pick()}
                search={m.edit_note_lang_search()}
                empty={m.edit_note_lang_empty()}
                class="block-8 inline-full"
                onValueChange={(chosen: string | null) => {
                  lang = chosen;
                }}
              />
            {/if}
          </div>
          <div class="grid content-start gap-1">
            <span id="ws-bd-quality-label" class="text-xs text-muted-foreground"
              >{m.ws_bd_quality()}</span
            >
            <Select.Root
              type="single"
              value={quality}
              disabled={isLocked}
              onValueChange={(chosen: string) => {
                quality =
                  qualities.find((item) => item === chosen) ?? "official";
              }}
            >
              <Select.Trigger
                class="text-sm block-8 inline-full"
                aria-labelledby="ws-bd-quality-label"
                >{qualityLabel[quality]()}</Select.Trigger
              >
              <Select.Content>
                {#each qualities as item (item)}
                  <Select.Item value={item}>{qualityLabel[item]()}</Select.Item>
                {/each}
              </Select.Content>
            </Select.Root>
          </div>
        </div>
        <fieldset
          class="grid gap-x-4 gap-y-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)]"
          disabled={isLocked}
        >
          <legend class="sr-only">{m.edit_evidence()}</legend>
          <div class="grid content-start gap-1">
            <label for="ws-bd-title" class="text-xs text-muted-foreground"
              >{m.edit_url_title()}</label
            >
            <Input
              id="ws-bd-title"
              bind:value={cited.title}
              placeholder={m.ws_bd_title_placeholder()}
              maxlength={500}
              class="block-8"
            />
          </div>
          <div class="grid content-start gap-1">
            <label for="ws-bd-publisher" class="text-xs text-muted-foreground"
              >{m.edit_url_publisher()}</label
            >
            <Input
              id="ws-bd-publisher"
              bind:value={cited.publisher}
              placeholder={m.edit_url_publisher_placeholder()}
              maxlength={200}
              class="block-8"
            />
          </div>
          <div class="grid content-start gap-1">
            <span id="ws-bd-kind-label" class="text-xs text-muted-foreground"
              >{m.edit_url_kind()}</span
            >
            <Select.Root
              type="single"
              value={cited.kind}
              onValueChange={(next: string) => {
                const kind = sourceKinds.find((item) => item === next);
                if (kind != null) {
                  cited.kind = kind;
                }
              }}
            >
              <Select.Trigger
                class="text-sm block-8 inline-full"
                aria-labelledby="ws-bd-kind-label"
                >{sourceKindLabel[cited.kind]()}</Select.Trigger
              >
              <Select.Content>
                {#each sourceKinds as kind (kind)}
                  <Select.Item value={kind}
                    >{sourceKindLabel[kind]()}</Select.Item
                  >
                {/each}
              </Select.Content>
            </Select.Root>
          </div>
        </fieldset>
      {/if}
    </section>

    <section class="grid gap-2 border-bs p-4" aria-labelledby="ws-bd-map">
      <div class="flex flex-wrap items-baseline justify-between gap-x-4">
        <h2 id="ws-bd-map" class="font-semibold">{m.ws_bd_map()}</h2>
        {#if codes.length > 0}
          <p class="font-figures text-xs text-muted-foreground">
            {m.ws_bd_tally({
              matched: count(tally.matched),
              added: count(tally.new),
              excluded: count(tally.excluded),
            })}
          </p>
        {/if}
      </div>
      {#if problems.length > 0}
        <ul class="grid gap-1 border-s-2 border-destructive ps-3">
          {#each problems as problem (problem)}
            <li class="text-sm font-semibold text-pretty">{problem}</li>
          {/each}
        </ul>
      {/if}
      <div
        class={cn(
          "relative overflow-hidden rounded-sm bg-card ring-1 ring-border block-[min(36rem,60svh)]",
          { "animate-pulse motion-reduce:animate-none": isReading },
        )}
        aria-busy={isReading}
      >
        <div class="block-full inline-full" {@attach engine.mount}></div>
        {#if shaped?.map == null}
          <p
            class="absolute inset-0 grid place-items-center p-6 text-center text-sm text-pretty text-muted-foreground"
          >
            {cited.upload == null
              ? m.ws_bd_map_empty()
              : scheme === ""
                ? m.ws_bd_needs_scheme()
                : m.ws_bd_map_reading()}
          </p>
        {/if}
        {#if hover != null && hovered != null}
          <div
            class="pointer-events-none absolute z-10 grid gap-0.5 rounded-sm bg-card/95 px-3 py-2 text-[0.8125rem] shadow-sm ring-1 ring-border min-inline-40"
            style:inset-inline-start="{hover.x + 14}px"
            style:inset-block-start="{hover.y + 14}px"
          >
            <span class="font-semibold"
              ><span class="font-figures"
                >{hovered.code ?? m.ws_bd_no_code()}</span
              >{#if (hovered.name ?? hovered.label) != null}<span class="ms-1.5"
                  >{hovered.name ?? hovered.label}</span
                >{/if}</span
            >
            {#if field !== ""}
              <span class="text-xs text-muted-foreground"
                >{statusLabel[
                  statusOf(hovered)
                ]()}{#if !isLocked && hovered.code != null}.
                  {excluded.has(hovered.code)
                    ? m.ws_bd_click_include()
                    : m.ws_bd_click_exclude()}{/if}</span
              >
            {/if}
          </div>
        {/if}
      </div>
      <BoundaryKey
        kinds={[
          "current",
          "upload",
          "new",
          "moved",
          "gap",
          "overlap",
          "parent",
        ]}
      />
      {#if drawn == null && codes.length > 0}
        <p class="text-xs text-pretty text-muted-foreground">
          {m.ws_bd_map_hint()}
        </p>
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
      <p class="pbe-2 text-xs text-muted-foreground">
        {m.ws_bd_diff_summary({ count: changed.length })}
        {#if added > 0}{m.ws_bd_diff_added({ count: added })}{/if}
      </p>
      <table class="text-sm inline-full">
        <thead class="text-xs text-muted-foreground">
          <tr class="border-be">
            <th class="py-1 text-start font-normal">{m.ws_bd_diff_area()}</th>
            <th class="py-1 text-end font-normal">{m.ws_bd_diff_km2()}</th>
          </tr>
        </thead>
        <tbody>
          {#each changed as row (`${row.key.area_id}|${row.key.valid_from}`)}
            {const named = $derived(labels.get(row.key.area_id ?? ""))}
            {const area = $derived(row.fields.area_km2)}
            <tr class="border-be">
              <td class="py-1.5 pe-2">
                <button
                  type="button"
                  class="text-start hover:underline"
                  onclick={() => {
                    const item = codes.find(
                      (entry) => entry.code === named?.code,
                    );
                    show(named?.code ?? null, item?.box ?? null);
                  }}
                >
                  <span class="font-figures font-semibold">{named?.code}</span>
                  <span class="ms-1">{named?.name}</span>
                </button>
                {#if row.op === "add"}
                  <span class="ms-1 text-xs font-semibold"
                    >{m.ws_bd_status_new()}</span
                  >
                {/if}
              </td>
              <td class="py-1.5 text-end font-figures whitespace-nowrap">
                {#if area != null}
                  {#if area[0] != null && area[0] !== area[1]}<span
                      class="text-muted-foreground"
                      >{count(Number(area[0]))} →</span
                    >{/if}
                  {count(Number(area[1]))}
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  {/snippet}

  {#snippet evidence()}
    {#if cited.upload == null}
      <p class="text-pretty text-muted-foreground">{m.ws_bd_needs_file()}</p>
    {:else}
      <dl class="grid gap-2">
        <div>
          <dt class="text-xs text-muted-foreground">{m.ws_bd_file()}</dt>
          <dd class="font-semibold wrap-break-word">
            {shaped?.filename ?? initial.upload?.filename ?? ""}
          </dd>
        </div>
        <div>
          <dt class="text-xs text-muted-foreground">{m.edit_url_title()}</dt>
          <dd>{cited.title.trim() === "" ? "" : cited.title}</dd>
        </div>
        <div>
          <dt class="text-xs text-muted-foreground">
            {m.edit_url_publisher()}
          </dt>
          <dd>{cited.publisher}</dd>
        </div>
        <div>
          <dt class="text-xs text-muted-foreground">{m.edit_url_kind()}</dt>
          <dd>{sourceKindLabel[cited.kind]()}</dd>
        </div>
      </dl>
      {#if source == null}
        <p class="mbs-3 text-xs font-semibold text-pretty">
          {m.ws_bd_needs_source()}
        </p>
      {/if}
    {/if}
  {/snippet}

  {#snippet commands(act: (action: () => void) => void)}
    {#if cited.upload != null}
      <Command.Item disabled={isLocked} onSelect={() => act(replace)}
        >{m.upload_replace()}</Command.Item
      >
    {/if}
    {#if exclude.length > 0}
      <Command.Item
        disabled={isLocked}
        onSelect={() => act(() => (exclude = []))}
        >{m.ws_bd_include_all()}</Command.Item
      >
    {/if}
    {#if shaped?.map != null}
      {const frame = $derived(shaped.map.frame)}
      <Command.Item onSelect={() => act(() => show(null, frame))}
        >{m.ws_bd_fit()}</Command.Item
      >
    {/if}
  {/snippet}
</Shell>
