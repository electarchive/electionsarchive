<script lang="ts">
  import { page } from "$app/state";
  import PencilIcon from "@lucide/svelte/icons/pencil";
  import type EditSheet from "#lib/components/contrib/edit-sheet.svelte";
  import { keep } from "#lib/linked.js";
  import { m } from "#lib/paraglide/messages.js";
  import { editable } from "#lib/search.svelte.js";

  const {
    entity,
    label,
    title,
  }: { entity: string; label: string; title: string } = $props();

  const open = $derived(
    (page.shallow?.url ?? page.url).searchParams.has("edit"),
  );

  let Sheet = $state<typeof EditSheet | null>(null);
  let loading: Promise<void> | undefined;

  function load() {
    loading ??= import("#lib/components/contrib/edit-sheet.svelte").then(
      (module) => {
        Sheet = module.default;
      },
    );
  }

  $effect(() => {
    editable.entity = entity;
    return () => {
      if (editable.entity === entity) {
        editable.entity = null;
      }
    };
  });

  $effect(() => {
    if (open) {
      load();
    }
  });

  const keydown = (event: KeyboardEvent) => {
    const target = event.target;
    if (
      event.key === "e" &&
      !open &&
      !event.metaKey &&
      !event.ctrlKey &&
      !event.altKey &&
      !(
        target instanceof HTMLElement &&
        (target.isContentEditable ||
          ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
      )
    ) {
      event.preventDefault();
      keep({ edit: "" });
    }
  };
</script>

<svelte:window onkeydown={keydown} />

<button
  type="button"
  class="inline-flex shrink-0 items-center gap-1.5 rounded-sm border border-input px-2.5 text-[0.8125rem] font-semibold block-8 hover:bg-secondary"
  aria-expanded={open}
  aria-keyshortcuts="e"
  onpointerenter={load}
  onfocus={load}
  onclick={() => keep({ edit: "" })}
  ><PencilIcon class="block-3.5 inline-3.5" />{m.edit_action()}</button
>

{#if open && Sheet != null}
  <Sheet {entity} {label} {title} />
{/if}
