<script lang="ts">
  import { page } from "$app/state";
  import { count, numbers, percent } from "#lib/format.js";
  import { lead } from "#lib/live/format.js";
  import methodology from "#lib/live/methodology.json";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import Calibration from "./calibration.svelte";
  import Checkpoints from "./checkpoints.svelte";
  import Draws from "./draws.svelte";
  import Replay from "./replay.svelte";
  import Tails from "./tails.svelte";
  import Turnout from "./turnout.svelte";

  const blue = swatch("#1a5fb4", "party:01a13afb-20af-70c4-bd13-85bcf33685ce");
  const red = swatch("#c01c28", "party:01a13afb-20af-70c4-bd13-85c13fd28fab");

  const { score } = methodology;
  const tails = methodology.tails.map(
    ([state, share]) => [String(state), Number(share)] as [string, number],
  );
  const brier = methodology.checkpoints.map((row) => ({
    q: row.q,
    value: row.brier,
  }));
  const cover = methodology.checkpoints.map((row) => ({
    q: row.q,
    value: row.cover,
  }));

  const sections = [
    { id: "how", title: m.method_how_title() },
    { id: "turnout", title: m.method_turnout_title() },
    { id: "margin", title: m.method_margin_title() },
    { id: "count-order", title: m.method_order_title() },
    { id: "replay", title: m.method_hero_title() },
    { id: "ranges", title: m.method_ranges_title() },
    { id: "testing", title: m.method_testing_title() },
    { id: "limits", title: m.method_limits_title() },
  ];
  const steps = [
    { title: m.method_step_baseline_title(), body: m.method_step_baseline() },
    { title: m.method_step_turnout_title(), body: m.method_step_turnout() },
    { title: m.method_step_swing_title(), body: m.method_step_swing() },
    { title: m.method_step_simulate_title(), body: m.method_step_simulate() },
    { title: m.method_step_report_title(), body: m.method_step_report() },
  ];
  const effects = [
    m.method_margin_uniform(),
    m.method_margin_stronghold(),
    m.method_margin_size(),
    m.method_margin_traits(),
    m.method_margin_order(),
  ];
  const limits = [
    m.method_limit_pause(),
    m.method_limit_types(),
    m.method_limit_precincts(),
    m.method_limit_baseline(),
    m.method_limit_traits(),
    m.method_limit_order(),
  ];

  const score3 = (value: number) =>
    numbers({ minimumFractionDigits: 3, maximumFractionDigits: 3 }).format(
      value,
    );
  const measures: Array<{
    label: string;
    hint: string;
    show: (row: typeof score.all) => string;
  }> = [
    {
      label: m.method_score_brier(),
      hint: m.method_score_brier_hint(),
      show: (row) => score3(row.brier),
    },
    {
      label: m.method_score_log(),
      hint: m.method_score_log_hint(),
      show: (row) => score3(row.log),
    },
    {
      label: m.method_score_error(),
      hint: m.method_score_error_hint(),
      show: (row) => lead(row.error),
    },
    {
      label: m.method_score_cover(),
      hint: m.method_score_cover_hint(),
      show: (row) => percent(row.cover, 0),
    },
    {
      label: m.method_score_turnout(),
      hint: m.method_score_turnout_hint(),
      show: (row) => percent(row.turnout),
    },
    {
      label: m.method_score_turnout_cover(),
      hint: m.method_score_turnout_cover_hint(),
      show: (row) => percent(row.turnoutCover, 0),
    },
    {
      label: m.method_score_miss(),
      hint: m.method_score_miss_hint(),
      show: (row) =>
        m.method_score_of({
          count: count(row.miss[0] ?? 0),
          total: count(row.miss[1] ?? 0),
        }),
    },
  ];
</script>

<svelte:head>
  <title>{m.method_title()} | {m.wordmark()}</title>
  <meta name="description" content={m.method_lede()} />
  <link rel="canonical" href="{page.url.origin}{page.url.pathname}" />
</svelte:head>

<main class="mx-auto grid gap-10 px-4 pbs-8 pbe-20 max-inline-6xl">
  <header class="grid gap-3 max-inline-[68ch]">
    <h1 class="title">{m.method_title()}</h1>
    <p class="text-lg text-muted-foreground">{m.method_lede()}</p>
  </header>

  <div class="grid gap-10 lg:grid-cols-[12rem_minmax(0,1fr)] lg:gap-12">
    <nav
      aria-labelledby="contents-title"
      class="lg:sticky lg:inset-bs-6 lg:self-start"
    >
      <h2 id="contents-title" class="pbe-2 text-xs text-muted-foreground">
        {m.method_contents()}
      </h2>
      <ol class="grid gap-1 text-sm">
        {#each sections as section (section.id)}
          <li>
            <a
              href="#{section.id}"
              class="hover:underline focus-visible:underline">{section.title}</a
            >
          </li>
        {/each}
      </ol>
    </nav>

    <div class="grid gap-14">
      <section
        id="how"
        aria-labelledby="how-title"
        class="grid scroll-mbs-6 gap-5 border-bs-[3px] border-foreground pbs-3"
      >
        <h2 id="how-title" class="heading">{m.method_how_title()}</h2>
        <p class="max-inline-[68ch]">{m.method_how_body()}</p>
        <ol class="grid gap-px bg-border sm:grid-cols-5">
          {#each steps as step, position (step.title)}
            <li
              class="grid content-start gap-1.5 bg-background py-3 sm:pe-3 sm:not-first:ps-3"
            >
              <span class="font-figures text-2xl font-semibold tabular-nums"
                >{position + 1}</span
              >
              <span class="text-sm font-semibold">{step.title}</span>
              <span class="text-sm text-muted-foreground">{step.body}</span>
            </li>
          {/each}
        </ol>
      </section>

      <section
        id="turnout"
        aria-labelledby="turnout-title"
        class="grid scroll-mbs-6 gap-5 border-bs-[3px] border-foreground pbs-3"
      >
        <h2 id="turnout-title" class="heading">{m.method_turnout_title()}</h2>
        <div class="grid gap-3 max-inline-[68ch]">
          <p>{m.method_turnout_body()}</p>
          <p>{m.method_turnout_body_more()}</p>
        </div>
        <Turnout />
      </section>

      <section
        id="margin"
        aria-labelledby="margin-title"
        class="grid scroll-mbs-6 gap-5 border-bs-[3px] border-foreground pbs-3"
      >
        <h2 id="margin-title" class="heading">{m.method_margin_title()}</h2>
        <div class="grid gap-3 max-inline-[68ch]">
          <p>{m.method_margin_body()}</p>
          <ul class="grid list-disc gap-1.5 ps-5">
            {#each effects as effect (effect)}
              <li>{effect}</li>
            {/each}
          </ul>
          <p>{m.method_margin_body_more()}</p>
        </div>
      </section>

      <section
        id="count-order"
        aria-labelledby="order-title"
        class="grid scroll-mbs-6 gap-5 border-bs-[3px] border-foreground pbs-3"
      >
        <h2 id="order-title" class="heading">{m.method_order_title()}</h2>
        <div class="grid gap-3 max-inline-[68ch]">
          <p>{m.method_order_body()}</p>
          <ul class="grid list-disc gap-1.5 ps-5">
            <li>{m.method_order_shift()}</li>
            <li>{m.method_order_swing()}</li>
            <li>{m.method_order_tail()}</li>
          </ul>
        </div>
        <div class="max-inline-xl">
          <Tails {tails} other={methodology.other} />
        </div>
      </section>

      <section
        id="replay"
        aria-labelledby="hero-title"
        class="grid scroll-mbs-6 gap-5 border-bs-[3px] border-foreground pbs-3"
      >
        <div class="grid gap-2 max-inline-[68ch]">
          <h2 id="hero-title" class="heading">{m.method_hero_title()}</h2>
          <p class="text-sm text-muted-foreground">{m.method_hero_caption()}</p>
        </div>
        <Replay
          final={methodology.replay.final}
          points={methodology.replay.points}
          {blue}
          {red}
        />
        <p class="text-sm max-inline-[68ch]">{m.method_hero_miss()}</p>
      </section>

      <section
        id="ranges"
        aria-labelledby="ranges-title"
        class="grid scroll-mbs-6 gap-5 border-bs-[3px] border-foreground pbs-3"
      >
        <h2 id="ranges-title" class="heading">{m.method_ranges_title()}</h2>
        <div class="grid gap-3 max-inline-[68ch]">
          <p>{m.method_ranges_body()}</p>
          <p>{m.method_ranges_status()}</p>
        </div>
        <Draws
          values={methodology.draws.values}
          {blue}
          {red}
          names={["Biden", "Trump"]}
        />
      </section>

      <section
        id="testing"
        aria-labelledby="testing-title"
        class="grid scroll-mbs-6 gap-6 border-bs-[3px] border-foreground pbs-3"
      >
        <h2 id="testing-title" class="heading">{m.method_testing_title()}</h2>
        <div class="grid gap-3 max-inline-[68ch]">
          <p>{m.method_testing_body({ races: count(methodology.races) })}</p>
          <p class="font-semibold">
            {m.method_testing_summary({
              brier: score3(score.all.brier),
              error: lead(score.all.error),
            })}
          </p>
          <p>
            {m.method_testing_real({
              races: count(methodology.nights.races),
              later: count(methodology.nights.later),
            })}
          </p>
          <p>
            {m.method_testing_real_result({
              cover: percent(score.real.cover, 0),
              turnout: percent(score.real.turnoutCover, 0),
            })}
          </p>
        </div>
        <div class="grid gap-8 md:grid-cols-3">
          <Checkpoints
            title={m.method_brier_chart()}
            note={m.method_brier_note()}
            series={brier}
            low={0}
            high={0.08}
            ticks={[0, 0.04, 0.08]}
            kind="score"
          />
          <Checkpoints
            title={m.method_cover_chart()}
            note={m.method_cover_note()}
            series={cover}
            low={0.6}
            high={1}
            target={0.8}
            ticks={[0.6, 0.8, 1]}
            kind="share"
          />
          <Calibration bins={methodology.calibration} />
        </div>

        <div class="grid gap-3">
          <h3 class="text-base font-semibold">{m.method_score_title()}</h3>
          <div class="overflow-x-auto">
            <table class="text-sm inline-full min-inline-160">
              <thead>
                <tr class="border-bs-[3px] border-foreground">
                  <th
                    scope="col"
                    class="py-2 pe-3 text-start align-bottom font-semibold"
                  >
                    {m.method_score_metric()}
                  </th>
                  <th scope="col" class="py-2 ps-3 text-end font-semibold"
                    >{m.method_score_all()}</th
                  >
                  <th scope="col" class="py-2 ps-3 text-end font-semibold"
                    >{m.method_score_close()}</th
                  >
                  <th scope="col" class="py-2 ps-3 text-end font-semibold"
                    >{m.method_score_real()}</th
                  >
                </tr>
              </thead>
              <tbody>
                {#each measures as measure (measure.label)}
                  <tr class="border-be">
                    <th
                      scope="row"
                      class="grid py-2 pe-3 text-start font-normal"
                    >
                      <span class="font-semibold">{measure.label}</span>
                      <span class="text-xs text-muted-foreground"
                        >{measure.hint}</span
                      >
                    </th>
                    <td
                      class="py-2 ps-3 text-end font-figures font-semibold tabular-nums"
                      >{measure.show(score.all)}</td
                    >
                    <td
                      class="py-2 ps-3 text-end font-figures font-semibold tabular-nums"
                      >{measure.show(score.close)}</td
                    >
                    <td
                      class="py-2 ps-3 text-end font-figures font-semibold tabular-nums"
                      >{measure.show(score.real)}</td
                    >
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        </div>
        <p class="max-inline-[68ch]">
          {m.method_testing_repo()}
          <a
            href="https://github.com/electarchive/live-forecast"
            class="underline underline-offset-2 hover:no-underline focus-visible:no-underline"
            >{m.method_testing_repo_link()}</a
          >
        </p>
      </section>

      <section
        id="limits"
        aria-labelledby="limits-title"
        class="grid scroll-mbs-6 gap-5 border-bs-[3px] border-foreground pbs-3"
      >
        <h2 id="limits-title" class="heading">{m.method_limits_title()}</h2>
        <ul class="grid gap-3 max-inline-[68ch]">
          {#each limits as limit (limit)}
            <li class="border-is-2 border-foreground ps-3">{limit}</li>
          {/each}
        </ul>
      </section>
    </div>
  </div>
</main>
