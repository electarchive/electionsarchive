<script lang="ts">
  import PlusIcon from "@lucide/svelte/icons/plus";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import * as Select from "#lib/components/ui/select/index.js";
  import { sourceKindLabel, sourceKinds } from "#lib/contrib.js";
  import { m } from "#lib/paraglide/messages.js";

  let {
    cited = $bindable(),
    isLocked,
    empty,
  }: {
    cited: {
      url: string;
      title: string;
      publisher: string;
      kind: (typeof sourceKinds)[number];
    } | null;
    isLocked: boolean;
    empty: string;
  } = $props();
</script>

{#if cited == null}
  <p class="text-pretty text-muted-foreground">{empty}</p>
  {#if !isLocked}
    <Button
      variant="outline"
      size="sm"
      class="mbs-3"
      onclick={() =>
        (cited = { url: "", title: "", publisher: "", kind: "official_body" })}
      ><PlusIcon />{m.ws_evidence_add()}</Button
    >
  {/if}
{:else}
  {const chosen = $derived(cited.kind)}
  <fieldset class="contrib-settle grid gap-2" disabled={isLocked}>
    <legend class="pbe-2 font-semibold">{m.edit_evidence()}</legend>
    <Input
      type="url"
      inputmode="url"
      bind:value={cited.url}
      placeholder="https://"
      aria-label={m.edit_url()}
      maxlength={2048}
      class="block-8"
    />
    <Input
      bind:value={cited.title}
      placeholder={m.edit_url_title_placeholder()}
      aria-label={m.edit_url_title()}
      maxlength={500}
      class="block-8"
    />
    <Input
      bind:value={cited.publisher}
      placeholder={m.edit_url_publisher_placeholder()}
      aria-label={m.edit_url_publisher()}
      maxlength={200}
      class="block-8"
    />
    <Select.Root
      type="single"
      value={chosen}
      onValueChange={(next: string) => {
        const kind = sourceKinds.find((item) => item === next);
        if (kind != null && cited != null) {
          cited.kind = kind;
        }
      }}
    >
      <Select.Trigger
        class="text-sm block-8 inline-full"
        aria-label={m.edit_url_kind()}
        >{sourceKindLabel[chosen]()}</Select.Trigger
      >
      <Select.Content>
        {#each sourceKinds as kind (kind)}
          <Select.Item value={kind}>{sourceKindLabel[kind]()}</Select.Item>
        {/each}
      </Select.Content>
    </Select.Root>
    <p class="text-xs text-pretty text-muted-foreground">
      {m.edit_url_note()}
    </p>
    {#if !isLocked}
      <Button
        variant="ghost"
        size="sm"
        class="justify-self-start px-2 text-xs text-muted-foreground"
        onclick={() => (cited = null)}>{m.ws_evidence_remove()}</Button
      >
    {/if}
  </fieldset>
{/if}
