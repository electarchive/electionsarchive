<script lang="ts">
  import { READ_ONLY } from "$app/env/public";
  import { CopyIcon } from "@lucide/svelte";
  import { toast } from "svelte-sonner";
  import { connect, INSTALL } from "#lib/agents.js";
  import { copy } from "#lib/clipboard.js";
  import AgentsScene from "#lib/components/agents-scene.svelte";
  import ShareImage from "#lib/components/share-image.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Tabs } from "#lib/components/ui/tabs/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();

  const setup = $derived(connect(data.origin));
  const clients = $derived(setup.clients);
  let client = $state("claude-code");
  const chosen = $derived(
    clients.find((entry) => entry.value === client) ?? clients[0],
  );
  const questions = $derived(setup.questions);
  const links = $derived([
    { href: "/api/v1/openapi.json", label: m.agents_rest(), text: "/api/v1" },
    { href: "/agents/skill.md", label: m.agents_skill(), text: "skill.md" },
    { href: "/llms.txt", label: "llms.txt", text: "llms.txt" },
  ]);
  const lede = $derived(
    READ_ONLY ? m.agents_lede_read_only() : m.agents_lede(),
  );

  function copyText(text: string) {
    void copy(text).then(() => toast.success(m.agents_copied()));
  }
</script>

<svelte:head>
  <title>{m.agents_title()} | {m.wordmark()}</title>
  <meta name="description" content={lede} />
  <meta property="og:title" content={m.agents_title()} />
  <meta property="og:description" content={lede} />
</svelte:head>

<ShareImage src="{data.origin}/og/agents.png" alt={m.agents_share_alt()} />

{#snippet code(text: string, label: string)}
  <div class="grid field grid-cols-[minmax(0,1fr)_auto] items-start">
    <pre
      class="overflow-x-auto px-3 py-2.5 font-figures text-[0.8125rem] leading-relaxed">{text}</pre>
    <Button
      variant="ghost"
      size="sm"
      class="m-1"
      aria-label="{m.agents_copy()}: {label}"
      onclick={() => copyText(text)}
    >
      <CopyIcon />{m.agents_copy()}
    </Button>
  </div>
{/snippet}

<main class="mx-auto grid gap-10 px-4 pbs-6 pbe-24 max-inline-6xl max-sm:px-3">
  <header class="grid gap-2 max-inline-[62ch]">
    <h1 class="title">{m.agents_title()}</h1>
    <p class="text-[0.9375rem] text-pretty text-muted-foreground">{lede}</p>
  </header>

  <figure class="scene grid border-be">
    <AgentsScene />
    <figcaption class="sr-only">{m.agents_scene()}</figcaption>
  </figure>

  <div
    class="grid gap-x-12 gap-y-10 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]"
  >
    <div class="grid content-start gap-10">
      <section class="grid gap-3" aria-labelledby="skill">
        <h2 id="skill" class="heading">{m.agents_skill_title()}</h2>
        <p class="text-sm text-pretty text-muted-foreground">
          {m.agents_skill_body()}
        </p>
        {@render code(INSTALL, m.agents_skill_title())}
      </section>

      <section class="grid gap-3" aria-labelledby="server">
        <h2 id="server" class="heading">{m.agents_server_title()}</h2>
        <p class="text-sm text-pretty text-muted-foreground">
          {m.agents_server_body()}
        </p>
        <Tabs
          id="client"
          panel="client-panel"
          label={m.agents_server_title()}
          options={clients.map(({ value, label }) => ({ value, label }))}
          bind:value={client}
        />
        <div
          id="client-panel"
          role="tabpanel"
          aria-labelledby="client-{client}"
          class="grid gap-2"
        >
          {#if chosen != null}
            {#if chosen.body != null}
              <p class="text-sm text-pretty">{chosen.body}</p>
            {/if}
            {@render code(chosen.code, chosen.label)}
          {/if}
        </div>
        <p class="text-sm text-pretty text-muted-foreground">
          {m.agents_key_body()}
          <a
            class="font-semibold text-foreground underline underline-offset-4"
            href="/settings/keys">{m.agents_step_key_link()}</a
          >
        </p>
      </section>
    </div>

    <section class="grid content-start gap-3" aria-labelledby="ask">
      <h2 id="ask" class="heading">{m.agents_ask_title()}</h2>
      <ul class="grid border-bs">
        {#each questions as question (question)}
          <li
            class="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-x-2 border-be py-2.5 text-[0.9375rem] text-pretty"
          >
            <span class="text-muted-foreground" aria-hidden="true">“</span
            >{question}
          </li>
        {/each}
      </ul>
    </section>
  </div>

  <section class="grid gap-3" aria-labelledby="more">
    <h2 id="more" class="heading">{m.agents_more_title()}</h2>
    <dl class="grid gap-x-8 gap-y-2 text-sm sm:grid-cols-3">
      {#each links as link (link.href)}
        <div class="grid gap-0.5">
          <dt class="text-muted-foreground">{link.label}</dt>
          <dd>
            <a
              class="font-figures font-semibold break-all underline decoration-border underline-offset-4 hover:decoration-foreground"
              href={link.href}>{link.text}</a
            >
          </dd>
        </div>
      {/each}
    </dl>
    <details class="group border-bs pbs-2">
      <summary
        class="cursor-pointer py-1 text-sm font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
      >
        {m.agents_tools_count({ count: data.tools.length })}
      </summary>
      <dl class="grid pbs-2">
        {#each data.tools as tool (tool.name)}
          <div
            class="grid gap-x-4 gap-y-0.5 border-be py-2 text-sm sm:grid-cols-[14rem_minmax(0,1fr)]"
          >
            <dt>
              <code class="font-figures font-semibold">{tool.name}</code>
              {#if tool.isKeyed}<span class="text-xs text-muted-foreground"
                  >{m.agents_key()}</span
                >{/if}
            </dt>
            <dd class="text-pretty text-muted-foreground">
              {tool.description}
            </dd>
          </div>
        {/each}
      </dl>
    </details>
  </section>
</main>
