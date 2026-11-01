<script lang="ts">
  import { resolve } from "$app/paths";
  import { ArrowUpRightIcon } from "@lucide/svelte";
  import Monogram from "#lib/components/monogram.svelte";
  import Shahmaran from "#lib/components/shahmaran.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const GITHUB = "https://github.com/electarchive/electionsarchive";

  const groups = [
    {
      title: m.footer_archive(),
      links: [
        { href: resolve("/coverage"), label: m.coverage_nav() },
        { href: resolve("/compare"), label: m.versus_action() },
        { href: resolve("/live/methodology"), label: m.footer_live_model() },
      ],
    },
    {
      title: m.footer_data(),
      links: [
        {
          href: resolve("/[slug=project]", { slug: "license" }),
          label: m.license_title(),
        },
        { href: "/api/v1/openapi.json", label: m.footer_api() },
        { href: resolve("/agents"), label: m.agents_nav() },
      ],
    },
    {
      title: m.footer_project(),
      links: [
        {
          href: resolve("/[slug=project]", { slug: "about" }),
          label: m.about_title(),
        },
        {
          href: resolve("/[slug=project]", { slug: "architecture" }),
          label: m.architecture_title(),
        },
        {
          href: resolve("/[slug=project]", { slug: "contribute" }),
          label: m.contribute_title(),
        },
        { href: GITHUB, label: m.footer_github() },
      ],
    },
  ];

  let status = $state<"idle" | "sending" | "done" | "failed">("idle");

  async function subscribe(
    event: SubmitEvent & { currentTarget: HTMLFormElement },
  ) {
    event.preventDefault();
    const form = event.currentTarget;
    status = "sending";
    try {
      await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        mode: "no-cors",
      });
      form.reset();
      status = "done";
    } catch {
      status = "failed";
    }
  }
</script>

<footer class="border-bs-[3px] border-foreground">
  <div
    class="mx-auto grid gap-x-12 gap-y-8 px-4 pbs-8 pbe-6 max-inline-7xl lg:grid-cols-[minmax(0,1fr)_auto_minmax(0,19rem)] max-sm:px-3"
  >
    <div class="grid content-start gap-2">
      <a
        href={resolve("/")}
        class="flex items-center gap-2.5 justify-self-start focus-visible:outline-2 focus-visible:outline-ring"
      >
        <Monogram size={22} />
        <span class="text-base font-[780] tracking-[-0.015em]"
          >{m.wordmark()}</span
        >
      </a>
      <p
        class="font-serif text-[0.9375rem]/relaxed text-pretty text-muted-foreground max-inline-[36ch]"
      >
        {m.footer_tagline()}
      </p>
      <p class="text-sm text-muted-foreground">
        <a
          href={resolve("/[slug=project]", { slug: "license" })}
          class="underline decoration-border underline-offset-4 hover:text-foreground hover:decoration-current focus-visible:outline-2 focus-visible:outline-ring"
          >{m.footer_licenses()}</a
        >
      </p>
    </div>

    <nav
      aria-label={m.footer_project()}
      class="grid grid-cols-3 gap-x-10 gap-y-6 max-sm:grid-cols-2 max-sm:gap-x-6"
    >
      {#each groups as group (group.title)}
        <div class="grid content-start gap-2">
          <h2 class="text-xs font-semibold text-muted-foreground">
            {group.title}
          </h2>
          <ul class="grid gap-1.5 text-sm">
            {#each group.links as link (link.href)}
              <li>
                <a
                  href={link.href}
                  rel={link.href === GITHUB ? "external" : undefined}
                  class="inline-flex items-center gap-0.5 underline decoration-transparent underline-offset-4 hover:decoration-current focus-visible:outline-2 focus-visible:outline-ring"
                  >{link.label}{#if link.href === GITHUB}<ArrowUpRightIcon
                      class="text-muted-foreground block-3.5 inline-3.5"
                      aria-hidden="true"
                    />{/if}</a
                >
              </li>
            {/each}
          </ul>
        </div>
      {/each}
    </nav>

    <form
      method="post"
      action="https://buttondown.com/api/emails/embed-subscribe/firatciftci"
      target="_blank"
      class="grid content-start gap-2"
      onsubmit={subscribe}
    >
      <label
        for="footer-email"
        class="text-xs font-semibold text-muted-foreground"
        >{m.footer_updates_title()}</label
      >
      <p id="footer-email-hint" class="text-sm text-muted-foreground">
        {m.footer_updates_hint()}
      </p>
      <input type="hidden" name="embed" value="1" />
      <input type="hidden" name="tag" value="electionsarchive" />
      <div class="flex gap-1.5">
        <Input
          id="footer-email"
          name="email"
          type="email"
          required
          autocomplete="email"
          placeholder={m.footer_email_placeholder()}
          aria-describedby="footer-email-hint footer-email-status"
          class="flex-1 bg-card block-8"
        />
        <Button type="submit" disabled={status === "sending"}
          >{status === "sending"
            ? m.footer_subscribing()
            : m.footer_subscribe()}</Button
        >
      </div>
      <p
        id="footer-email-status"
        role="status"
        class={cn("text-sm min-block-5", {
          "text-destructive": status === "failed",
        })}
      >
        {#if status === "done"}
          {m.footer_subscribed()}
        {:else if status === "failed"}
          {m.footer_subscribe_failed()}
        {/if}
      </p>
    </form>
  </div>

  <div class="border-bs">
    <div
      class="mx-auto flex flex-wrap items-center gap-x-5 gap-y-2 p-4 text-sm text-muted-foreground max-inline-7xl max-sm:px-3"
    >
      <a
        href="https://www.firatciftci.com"
        rel="external"
        class="group flex items-center gap-2.5 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
      >
        <Shahmaran
          class="shrink-0 rotate-20 opacity-30 transition-[rotate,opacity] duration-700 inline-7 group-hover:rotate-[8deg] group-hover:opacity-70 group-focus-visible:rotate-[8deg] group-focus-visible:opacity-70 motion-reduce:transition-none"
        />
        <span
          >{m.footer_credit()}
          <span
            class="font-semibold text-foreground underline decoration-border underline-offset-4 group-hover:decoration-current"
            >Firat Ciftci</span
          ></span
        >
      </a>
      <span class="ms-auto max-sm:ms-0">{m.footer_rights()}</span>
      <a
        href={resolve("/[slug=project]", { slug: "privacy" })}
        class="underline decoration-transparent underline-offset-4 hover:text-foreground hover:decoration-current focus-visible:outline-2 focus-visible:outline-ring"
        >{m.privacy_title()}</a
      >
      <a
        href={resolve("/[slug=project]", { slug: "terms" })}
        class="underline decoration-transparent underline-offset-4 hover:text-foreground hover:decoration-current focus-visible:outline-2 focus-visible:outline-ring"
        >{m.terms_title()}</a
      >
    </div>
  </div>
</footer>
