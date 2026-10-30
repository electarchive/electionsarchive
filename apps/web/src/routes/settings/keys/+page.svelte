<script lang="ts">
  import { enhance } from "$app/forms";
  import { CopyIcon } from "@lucide/svelte";
  import { toast } from "svelte-sonner";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { Label } from "#lib/components/ui/label/index.js";
  import * as Select from "#lib/components/ui/select/index.js";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getConfirmationContext } from "#lib/stores/confirmation.svelte.js";
  import { cn } from "#lib/utils.js";
  import type { ActionData, PageData } from "./$types";

  const { data, form }: { data: PageData; form: ActionData } = $props();

  const LIFETIMES = [
    { value: "30", label: m.keys_lifetime_30 },
    { value: "90", label: m.keys_lifetime_90 },
    { value: "365", label: m.keys_lifetime_365 },
    { value: "never", label: m.keys_lifetime_never },
  ] as const;

  const confirmation = getConfirmationContext();

  let lifetime = $state("90");
  let isCreating = $state(false);
  let revoking = $state<string | null>(null);

  const isFull = $derived(data.keys.length >= data.limit);
  const created = $derived(
    form != null && "created" in form ? form.created : null,
  );
  const problem = $derived(
    form != null && "message" in form ? form.message : null,
  );

  function day(value: string | null) {
    return date(value?.slice(0, 10) ?? null);
  }
</script>

<svelte:head>
  <title>{m.keys_title()} | {m.wordmark()}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="mx-auto grid gap-10 px-4 pbs-6 pbe-24 max-inline-3xl max-sm:px-3">
  <header class="grid gap-2">
    <h1 class="title">{m.keys_title()}</h1>
    <p
      class="text-[0.9375rem] text-pretty text-muted-foreground max-inline-[60ch]"
    >
      {m.keys_lede()}
    </p>
  </header>

  {#if created != null}
    <section
      class="grid gap-3 border-bs-[3px] border-be border-foreground pbs-4 pbe-5"
      aria-labelledby="created"
      role="status"
    >
      <div class="grid gap-1">
        <h2 id="created" class="heading">{m.keys_created_title()}</h2>
        <p class="text-sm text-pretty text-muted-foreground">
          <span class="font-semibold text-foreground">{created.name}</span>.
          {m.keys_created_note()}
        </p>
      </div>
      <div class="flex items-stretch gap-2 max-md:flex-col">
        <output
          class="flex flex-1 field items-center px-3 py-1.5 font-figures text-[0.9375rem] break-all select-all min-block-9"
          >{created.key}</output
        >
        <Button
          size="lg"
          onclick={() =>
            void navigator.clipboard
              .writeText(created.key)
              .then(() => toast.success(m.keys_copied()))}
        >
          <CopyIcon />{m.keys_copy()}
        </Button>
      </div>
    </section>
  {/if}

  <section class="grid gap-3" aria-labelledby="keys">
    <div class="flex items-baseline justify-between gap-4">
      <h2 id="keys" class="heading">{m.keys_yours()}</h2>
      <span class="font-figures text-sm text-muted-foreground"
        >{m.keys_count({ count: data.keys.length, limit: data.limit })}</span
      >
    </div>
    {#if data.keys.length === 0}
      <p class="border-bs pbs-3 text-sm text-muted-foreground">
        {m.keys_empty()}
      </p>
    {:else}
      <table class="border-bs text-sm inline-full">
        <thead class="max-sm:sr-only">
          <tr class="border-be text-muted-foreground">
            <th class="py-2 pe-4 text-start font-normal">{m.keys_col_name()}</th
            >
            <th class="py-2 pe-4 text-start font-normal"
              >{m.keys_col_agent()}</th
            >
            <th class="py-2 pe-4 text-start font-normal"
              >{m.keys_col_created()}</th
            >
            <th class="py-2 pe-4 text-start font-normal">{m.keys_col_used()}</th
            >
            <th class="py-2 pe-4 text-start font-normal"
              >{m.keys_col_expires()}</th
            >
            <th class="py-2"><span class="sr-only">{m.keys_revoke()}</span></th>
          </tr>
        </thead>
        <tbody>
          {#each data.keys as key (key.id)}
            <tr
              class={cn(
                "border-be align-baseline max-sm:grid max-sm:grid-cols-[1fr_auto] max-sm:gap-x-3 max-sm:py-2.5",
                { "text-muted-foreground": key.expired },
              )}
            >
              <td class="py-2 pe-4 max-sm:p-0">
                <span class="font-semibold text-foreground"
                  >{key.name ?? "–"}</span
                >
                <span class="block font-figures text-xs text-muted-foreground"
                  >{key.start ?? ""}…</span
                >
              </td>
              <td
                class="py-2 pe-4 max-sm:col-start-1 max-sm:p-0 max-sm:text-xs"
              >
                {key.agent_name ?? "–"}
                {#if key.agent_model != null}
                  <span class="text-muted-foreground sm:block sm:text-xs"
                    >{key.agent_model}</span
                  >
                {/if}
              </td>
              <td class="py-2 pe-4 font-figures whitespace-nowrap max-sm:hidden"
                >{day(key.created_at)}</td
              >
              <td class="py-2 pe-4 font-figures whitespace-nowrap max-sm:hidden"
                >{key.last_request == null
                  ? m.keys_never_used()
                  : day(key.last_request)}</td
              >
              <td
                class={cn(
                  "py-2 pe-4 font-figures whitespace-nowrap max-sm:col-start-1 max-sm:p-0 max-sm:text-xs",
                  { "font-semibold text-foreground": key.expired },
                )}
              >
                {#if key.expired}
                  {m.keys_expired()}
                {:else if key.expires_at == null}
                  <span class="max-sm:hidden">{m.keys_never_expires()}</span>
                {:else}
                  <span class="sm:hidden">{m.keys_col_expires()}</span>
                  {day(key.expires_at)}
                {/if}
              </td>
              <td
                class="py-2 text-end max-sm:col-start-2 max-sm:row-span-3 max-sm:row-start-1 max-sm:self-center max-sm:p-0"
              >
                <form
                  method="POST"
                  action="?/revoke"
                  use:enhance={async ({ cancel }) => {
                    if (
                      !(await confirmation.confirm({
                        title: m.keys_revoke_title({
                          name: key.name ?? key.start ?? "",
                        }),
                        description: m.keys_revoke_description(),
                        confirmText: m.keys_revoke(),
                        cancelText: m.keys_cancel(),
                        variant: "destructive",
                      }))
                    ) {
                      cancel();
                      return;
                    }
                    revoking = key.id;
                    return async ({ result, update }) => {
                      if (result.type === "failure") {
                        toast.error(String(result.data?.message ?? ""));
                      } else {
                        if (result.type === "success") {
                          toast.success(m.keys_revoked());
                        }
                        await update();
                      }
                      revoking = null;
                    };
                  }}
                >
                  <input type="hidden" name="id" value={key.id} />
                  <Button
                    type="submit"
                    variant="ghost"
                    size="sm"
                    class="text-destructive hover:text-destructive"
                    disabled={revoking === key.id}>{m.keys_revoke()}</Button
                  >
                </form>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </section>

  <section class="grid gap-4" aria-labelledby="new">
    <h2 id="new" class="heading">{m.keys_new()}</h2>
    <form
      method="POST"
      action="?/create"
      class="grid gap-x-4 gap-y-5 border-bs pbs-4 sm:grid-cols-2"
      use:enhance={() => {
        isCreating = true;
        return async ({ result, update }) => {
          await update({ reset: result.type === "success" });
          if (result.type === "success") {
            lifetime = "90";
          }
          isCreating = false;
        };
      }}
    >
      <div class="grid content-start gap-1.5">
        <Label for="name">{m.keys_field_name()}</Label>
        <Input
          id="name"
          name="name"
          required
          maxlength={60}
          autocomplete="off"
          aria-describedby="name-hint"
        />
        <p id="name-hint" class="text-xs text-muted-foreground">
          {m.keys_field_name_hint()}
        </p>
      </div>
      <div class="grid content-start gap-1.5">
        <Label for="lifetime">{m.keys_field_lifetime()}</Label>
        <Select.Root type="single" name="lifetime" bind:value={lifetime}>
          <Select.Trigger id="lifetime" class="inline-full"
            >{LIFETIMES.find(
              (item) => item.value === lifetime,
            )?.label()}</Select.Trigger
          >
          <Select.Content>
            {#each LIFETIMES as item (item.value)}
              <Select.Item value={item.value}>{item.label()}</Select.Item>
            {/each}
          </Select.Content>
        </Select.Root>
      </div>
      <div class="grid content-start gap-1.5">
        <Label for="agent-name">{m.keys_field_agent_name()}</Label>
        <Input
          id="agent-name"
          name="agentName"
          required
          maxlength={100}
          autocomplete="off"
          aria-describedby="agent-hint"
        />
      </div>
      <div class="grid content-start gap-1.5">
        <Label for="agent-model">{m.keys_field_agent_model()}</Label>
        <Input
          id="agent-model"
          name="agentModel"
          required
          maxlength={100}
          autocomplete="off"
          aria-describedby="agent-hint"
        />
      </div>
      <p
        id="agent-hint"
        class="-mbs-3 text-xs text-muted-foreground sm:col-span-2"
      >
        {m.keys_field_agent_hint()}
      </p>
      {#if problem != null}
        <p class="text-sm text-destructive sm:col-span-2" role="alert">
          {problem}
        </p>
      {:else if isFull}
        <p class="text-sm text-muted-foreground sm:col-span-2">
          {m.keys_limit({ count: data.limit })}
        </p>
      {/if}
      <div class="sm:col-span-2">
        <Button type="submit" size="lg" disabled={isCreating || isFull}
          >{m.keys_create()}</Button
        >
      </div>
    </form>
  </section>
</main>
