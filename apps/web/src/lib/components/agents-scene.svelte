<script lang="ts">
  const blue = "#4c8bf5";
  const red = "#ef5a4c";
  const gray = "#aeb4bd";
  const yellow = "#ffe03a";
  const glow = "#7fe0a8";
  const hues = [blue, red, gray, blue, red];

  function delay(seconds: number) {
    return `${seconds}s`;
  }

  const flags = Array.from({ length: 17 }, (_, index) => {
    const t = 0.04 + index * 0.0575;
    const x = 30 + 1140 * t;
    const y = 30 + 200 * t * (1 - t);
    return {
      d: `M${(x - 15).toFixed(1)} ${y.toFixed(1)} L${(x + 15).toFixed(1)} ${y.toFixed(1)} L${x.toFixed(1)} ${(y + 30).toFixed(1)} Z`,
      isMarker: index % 2 === 0,
      wait: delay((index % 5) * 0.4),
    };
  });

  function bars(x: number, base: number, heights: Array<number>, seed: number) {
    return heights.map((height, index) => ({
      x: x + index * 16,
      y: base - height,
      height,
      fill: hues[(index + seed) % hues.length] ?? gray,
      wait: delay(((seed * 7 + index * 3) % 10) / 10 + 0.9),
    }));
  }

  function seats(x: number, base: number) {
    return Array.from({ length: 12 }, (_, index) => {
      const angle = Math.PI - (index / 11) * Math.PI;
      return {
        cx: (x + 51 + Math.cos(angle) * 30).toFixed(1),
        cy: (base - 4 - Math.sin(angle) * 30).toFixed(1),
        fill: index < 5 ? blue : index < 6 ? gray : red,
        wait: delay(index * 0.15),
      };
    });
  }

  function cells(x: number, y: number) {
    const fills = [blue, red, red, blue, gray, blue, red, yellow];
    return fills.map((fill, index) => ({
      x: x + 16 + (index % 4) * 18,
      y: y + 14 + Math.floor(index / 4) * 22 + (index % 2) * 6,
      fill,
      wait: delay((index * 0.37) % 2),
    }));
  }

  const screens = Array.from({ length: 9 }, (_, index) => {
    const column = index % 3;
    const row = Math.floor(index / 3);
    const x = 838 + column * 112;
    const y = 120 + row * 86;
    return {
      x,
      y,
      base: y + 62,
      kind: (column + row * 3) % 5,
      seed: column + row,
    };
  });
</script>

<svg
  xmlns="http://www.w3.org/2000/svg"
  viewBox="0 0 1200 520"
  width="1200"
  height="520"
  aria-hidden="true"
>
  <g
    class="s-line"
    stroke="#15171a"
    stroke-width="3"
    stroke-linejoin="round"
    stroke-linecap="round"
  >
    <g class="piece" style:--i="0">
      <path fill="none" stroke-width="2" d="M30 30 Q600 230 1170 30" />
      {#each flags as flag (flag.d)}
        <g class="flag" style:--d={flag.wait}>
          {#if flag.isMarker}
            <path class="s-marker" fill={yellow} d={flag.d} />
          {:else}
            <path class="s-paper" fill="#ffffff" d={flag.d} />
          {/if}
        </g>
      {/each}
    </g>
    <line x1="20" y1="500" x2="1180" y2="500" />

    <g class="piece" style:--i="0">
      <rect
        class="s-tint"
        fill="#e9ebef"
        x="58"
        y="244"
        width="12"
        height="256"
      />
      <rect
        class="s-tint"
        fill="#e9ebef"
        x="252"
        y="244"
        width="12"
        height="256"
      />
      <rect
        class="s-ink"
        fill="#15171a"
        x="46"
        y="212"
        width="230"
        height="36"
        rx="5"
      />
      <path
        class="s-marker-line"
        fill="none"
        stroke={yellow}
        stroke-width="6"
        d="M146 229 l9 9 l17 -17"
      />
      <g class="sway">
        <path
          class="s-paper"
          fill="#ffffff"
          d="M70 248 H164 V446 q-11.75 12 -23.5 0 q-11.75 12 -23.5 0 q-11.75 12 -23.5 0 q-11.75 12 -23.5 0 Z"
        />
        <path
          fill="none"
          stroke-width="2"
          d="M93.5 256 V438 M117 256 V442 M140.5 256 V438"
        />
      </g>
    </g>
    <g class="piece" style:--i="3">
      <g class="bob" style:--d=".2s">
        <rect fill="#e98a62" x="184" y="460" width="13" height="40" rx="4" />
        <rect fill="#e98a62" x="214" y="460" width="13" height="40" rx="4" />
        <line x1="205" y1="378" x2="205" y2="358" />
        <circle class="s-marker" fill={yellow} cx="205" cy="352" r="7" />
        <rect fill="#e98a62" x="170" y="376" width="72" height="88" rx="14" />
        <g class="blink">
          <rect
            fill="#15171a"
            stroke="none"
            x="188"
            y="398"
            width="9"
            height="16"
            rx="2"
          />
          <rect
            fill="#15171a"
            stroke="none"
            x="214"
            y="398"
            width="9"
            height="16"
            rx="2"
          />
        </g>
        <path fill="none" stroke-width="2.5" d="M199 424 q6 5 12 0" />
      </g>
      <rect
        class="s-tint"
        fill="#e9ebef"
        x="160"
        y="440"
        width="94"
        height="9"
        rx="2"
      />
      <rect
        class="s-paper"
        fill="#ffffff"
        x="176"
        y="426"
        width="44"
        height="14"
        rx="1"
        transform="rotate(-4 198 433)"
      />
      <path
        fill="none"
        stroke="#15171a"
        stroke-width="2"
        d="M183 431 h6 M183 436 h6 M196 433 l3 3 l6 -6"
      />
      <g class="scribble">
        <path fill="none" stroke-width="6" d="M240 414 q14 8 -6 20" />
        <line
          class="s-marker-line"
          x1="232"
          y1="436"
          x2="220"
          y2="420"
          stroke={yellow}
          stroke-width="5"
        />
      </g>
    </g>

    <g class="piece" style:--i="1">
      <rect
        class="s-tint"
        fill="#e9ebef"
        x="530"
        y="410"
        width="236"
        height="12"
        rx="3"
      />
      <rect
        class="s-tint"
        fill="#e9ebef"
        x="546"
        y="422"
        width="10"
        height="78"
      />
      <rect
        class="s-tint"
        fill="#e9ebef"
        x="742"
        y="422"
        width="10"
        height="78"
      />
      <rect
        class="s-tint"
        fill="#e9ebef"
        x="690"
        y="378"
        width="10"
        height="34"
      />
      <rect
        class="s-ink"
        fill="#15171a"
        x="670"
        y="404"
        width="50"
        height="7"
        rx="2"
      />
      <rect x="636" y="290" width="118" height="88" rx="7" fill="#1d2733" />
      {#each bars(652, 362, [18, 30, 44, 26], 1) as bar (bar.x)}
        <rect
          class="bar"
          style:--d={bar.wait}
          x={bar.x}
          y={bar.y}
          width="11"
          height={bar.height}
          fill={bar.fill}
          stroke="none"
        />
      {/each}
      <path
        class="trace"
        fill="none"
        stroke={yellow}
        stroke-width="3"
        d="M720 350 l8 -12 l8 6 l8 -20"
      />
      <circle class="s-paper" fill="#ffffff" cx="640" cy="160" r="30" />
      <g class="minute">
        <line x1="640" y1="160" x2="640" y2="138" stroke-width="3" />
      </g>
      <line x1="640" y1="160" x2="627" y2="167" stroke-width="4" />
      <circle class="s-ink" fill="#15171a" cx="640" cy="160" r="3" />
    </g>
    <g class="piece" style:--i="5">
      <rect
        class="s-paper"
        fill="#ffffff"
        x="598"
        y="386"
        width="20"
        height="24"
        rx="3"
      />
      <path fill="none" stroke-width="2.5" d="M618 392 q9 0 9 7 t-9 7" />
      <path
        class="steam"
        fill="none"
        stroke-width="2"
        d="M604 378 q-4 -6 0 -12 t0 -12 M612 380 q-4 -6 0 -12 t0 -12"
      />
      <rect
        class="s-tint"
        fill="#e9ebef"
        x="500"
        y="440"
        width="64"
        height="10"
        rx="3"
      />
      <rect
        class="s-tint"
        fill="#e9ebef"
        x="527"
        y="450"
        width="10"
        height="50"
      />
      <g class="lean">
        <rect fill="#eef1f6" x="510" y="452" width="13" height="46" rx="4" />
        <rect fill="#eef1f6" x="540" y="452" width="13" height="46" rx="4" />
        <rect fill="#eef1f6" x="498" y="364" width="70" height="88" rx="18" />
        <rect fill="#eef1f6" x="524" y="350" width="18" height="16" />
        <rect fill="#eef1f6" x="492" y="292" width="82" height="62" rx="16" />
        <circle class="s-tint" fill="#e9ebef" cx="492" cy="323" r="6" />
        <circle class="s-tint" fill="#e9ebef" cx="574" cy="323" r="6" />
        <rect x="502" y="303" width="62" height="40" rx="8" fill="#1d2733" />
        <path
          fill="none"
          stroke={glow}
          stroke-width="3.5"
          d="M515 314 l9 8 l-9 8"
        />
        <rect
          class="cursor"
          x="530"
          y="328"
          width="13"
          height="4"
          fill={glow}
          stroke="none"
        />
        <path fill="none" stroke-width="6" d="M560 396 q24 -4 38 2" />
        <path fill="none" stroke-width="6" d="M506 400 q-8 26 12 38" />
      </g>
      <g>
        <circle class="s-paper" fill="#ffffff" cx="584" cy="276" r="4" />
        <circle class="s-paper" fill="#ffffff" cx="596" cy="262" r="6" />
        <rect
          class="s-paper"
          fill="#ffffff"
          x="600"
          y="214"
          width="66"
          height="36"
          rx="18"
        />
        {#each [618, 633, 648] as cx, index (cx)}
          <g class="dot" style:--d={delay(index * 0.2)}>
            <circle class="s-ink" fill="#15171a" {cx} cy="232" r="4" />
          </g>
        {/each}
      </g>
    </g>

    <g class="piece" style:--i="4">
      <g class="bob" style:--d="0s">
        <g class="leg">
          <rect fill="#63b59a" x="352" y="400" width="17" height="98" rx="6" />
        </g>
        <g class="leg leg-b">
          <path
            fill="#63b59a"
            d="M410 400 h17 v66 q0 15 14 15 h7 v17 h-12 q-26 0 -26 -27 Z"
          />
        </g>
        <rect fill="#63b59a" x="326" y="370" width="128" height="32" rx="12" />
        <g class="blink">
          <circle fill="#15171a" stroke="none" cx="372" cy="385" r="5" />
          <circle fill="#15171a" stroke="none" cx="408" cy="385" r="5" />
        </g>
        <path fill="none" stroke-width="2.5" d="M383 391 q7 6 14 0" />
        <path
          fill="none"
          stroke-width="5"
          d="M332 380 q-14 -12 0 -28 M448 380 q14 -12 0 -28"
        />
        <rect
          class="s-paper"
          fill="#ffffff"
          x="348"
          y="314"
          width="84"
          height="56"
          rx="3"
        />
        <rect
          class="s-ink"
          fill="#15171a"
          x="374"
          y="321"
          width="32"
          height="6"
          rx="1"
        />
        <path
          class="s-marker-line"
          fill="none"
          stroke={yellow}
          stroke-width="5"
          d="M379 346 l8 8 l15 -15"
        />
        <g class="drop">
          <rect
            class="s-paper"
            fill="#ffffff"
            x="381"
            y="290"
            width="18"
            height="26"
            rx="1"
          />
          <path fill="none" stroke-width="2" d="M385 298 h10 M385 304 h7" />
        </g>
      </g>
    </g>

    <g class="piece" style:--i="2">
      <path
        fill="none"
        stroke-width="2.5"
        d="M889 378 q-6 60 30 70 t40 52 M1001 378 q10 40 -8 70 M1113 378 q20 50 -10 80 t20 42"
      />
      {#each screens as screen (screen.x * 10 + screen.y)}
        <rect
          x={screen.x}
          y={screen.y}
          width="102"
          height="74"
          rx="6"
          fill="#1d2733"
        />
        {#if screen.kind === 0}
          {#each bars(screen.x + 14, screen.base, [38, 26, 12, 30], screen.seed) as bar (bar.x)}
            <rect
              class="bar"
              style:--d={bar.wait}
              x={bar.x}
              y={bar.y}
              width="11"
              height={bar.height}
              fill={bar.fill}
              stroke="none"
            />
          {/each}
        {:else if screen.kind === 1}
          <path
            class="trace"
            fill="none"
            stroke={glow}
            stroke-width="3"
            d="M{screen.x + 12} {screen.base -
              8} l14 -10 l12 6 l14 -20 l12 8 l14 -22 l12 4"
          />
        {:else if screen.kind === 2}
          {#each seats(screen.x, screen.base) as seat (seat.cx)}
            <circle
              class="flicker"
              style:--d={seat.wait}
              cx={seat.cx}
              cy={seat.cy}
              r="4.5"
              fill={seat.fill}
              stroke="none"
            />
          {/each}
        {:else if screen.kind === 3}
          <rect
            x={screen.x + 12}
            y={screen.y + 18}
            width="78"
            height="10"
            rx="2"
            fill={gray}
            stroke="none"
          />
          <rect
            class="meter"
            x={screen.x + 12}
            y={screen.y + 18}
            width="46"
            height="10"
            rx="2"
            fill={blue}
            stroke="none"
          />
          <rect
            x={screen.x + 12}
            y={screen.y + 38}
            width="78"
            height="10"
            rx="2"
            fill={gray}
            stroke="none"
          />
          <rect
            class="meter"
            style:--d=".6s"
            x={screen.x + 12}
            y={screen.y + 38}
            width="34"
            height="10"
            rx="2"
            fill={red}
            stroke="none"
          />
        {:else}
          <g stroke="none">
            {#each cells(screen.x, screen.y) as cell (cell.x * 1000 + cell.y)}
              <rect
                class="flicker"
                style:--d={cell.wait}
                x={cell.x}
                y={cell.y}
                width="15"
                height="18"
                rx="2"
                fill={cell.fill}
              />
            {/each}
          </g>
        {/if}
      {/each}
      <rect
        class="flash"
        x="1062"
        y="292"
        width="102"
        height="74"
        rx="6"
        fill={yellow}
        stroke="none"
        opacity="0"
      />
    </g>

    <g class="piece" style:--i="6">
      <g class="shake">
        <rect fill="#2c3038" x="976" y="476" width="15" height="24" rx="4" />
        <rect fill="#2c3038" x="1009" y="476" width="15" height="24" rx="4" />
        <rect fill="#2c3038" x="964" y="408" width="72" height="72" rx="12" />
        <path
          fill="none"
          stroke="#ffffff"
          stroke-width="4"
          d="M990 464 l20 -32"
        />
        <path
          fill="none"
          stroke-width="6"
          d="M968 420 q-28 -22 -12 -62 M1032 420 q28 -22 12 -62"
        />
        <rect fill="#2c3038" x="958" y="344" width="84" height="62" rx="16" />
        <rect x="968" y="358" width="64" height="30" rx="10" fill="#0e1014" />
        <circle fill="#ffffff" stroke="none" cx="987" cy="373" r="8" />
        <circle fill="#ffffff" stroke="none" cx="1013" cy="373" r="8" />
        <circle
          class="pupil"
          fill="#15171a"
          stroke="none"
          cx="987"
          cy="373"
          r="3"
        />
        <circle
          class="pupil"
          fill="#15171a"
          stroke="none"
          cx="1013"
          cy="373"
          r="3"
        />
        <path
          fill="none"
          stroke="#ffffff"
          stroke-width="2.5"
          d="M992 396 q8 -5 16 0"
        />
      </g>
      <path
        class="sweat"
        style:--d="0s"
        fill="#8fd0f5"
        stroke-width="2"
        d="M1052 340 q6 10 0 14 q-6 -4 0 -14 Z"
      />
      <path
        class="sweat"
        style:--d=".7s"
        fill="#8fd0f5"
        stroke-width="2"
        d="M946 348 q6 10 0 14 q-6 -4 0 -14 Z"
      />
      <g class="alarm">
        <rect
          class="s-marker"
          fill={yellow}
          x="1052"
          y="290"
          width="10"
          height="26"
          rx="3"
        />
        <circle class="s-marker" fill={yellow} cx="1057" cy="327" r="5" />
      </g>
    </g>

    <g class="piece" style:--i="7">
      <g transform="translate(118 62)">
        <g class="hover">
          <line x1="286" y1="118" x2="286" y2="136" stroke-width="2" />
          <rect
            class="s-paper"
            fill="#ffffff"
            x="274"
            y="136"
            width="24"
            height="30"
            rx="1"
          />
          <path
            class="s-marker-line"
            fill="none"
            stroke={yellow}
            stroke-width="3.5"
            d="M279 151 l5 5 l9 -9"
          />
          <path
            class="rotor"
            fill="none"
            stroke-width="3"
            d="M252 92 h26 M294 92 h26"
          />
          <rect fill="#eef1f6" x="262" y="94" width="48" height="26" rx="12" />
          <circle fill="#15171a" stroke="none" cx="278" cy="107" r="3.5" />
          <circle fill="#15171a" stroke="none" cx="294" cy="107" r="3.5" />
        </g>
      </g>
    </g>
  </g>
</svg>

<style>
  svg {
    display: block;
    inline-size: 100%;
    block-size: auto;
    overflow: visible;
  }
  svg * {
    transform-box: fill-box;
  }
  .s-line {
    stroke: var(--foreground);
  }
  .s-paper {
    fill: var(--background);
  }
  .s-tint {
    fill: var(--accent);
  }
  .s-ink {
    fill: var(--foreground);
  }
  .s-marker {
    fill: var(--marker);
  }
  .s-marker-line {
    stroke: var(--marker);
  }

  @media (prefers-reduced-motion: no-preference) {
    .piece {
      transform-origin: 50% 100%;
      animation: pop 0.7s cubic-bezier(0.2, 0.9, 0.3, 1.25) both;
      animation-delay: calc(var(--i) * 110ms);
    }
    .bob {
      animation: bob 2.4s ease-in-out infinite alternate;
      animation-delay: var(--d);
    }
    .hover {
      animation: bob 1.8s ease-in-out infinite alternate;
    }
    .rotor {
      transform-origin: center;
      animation: whirl 0.16s linear infinite alternate;
    }
    .blink {
      transform-origin: center;
      animation: blink 4.6s infinite;
    }
    .bar {
      transform-origin: 50% 100%;
      animation: count var(--d) ease-in-out infinite alternate;
    }
    .meter {
      transform-origin: 0 50%;
      animation: grow 2.6s ease-in-out infinite alternate;
      animation-delay: var(--d, 0s);
    }
    .trace {
      stroke-dasharray: 160;
      animation: draw 3.2s ease-in-out infinite;
    }
    .flicker {
      animation: flicker 2.2s steps(2, jump-none) infinite;
      animation-delay: var(--d);
    }
    .flash {
      animation: flash 3.8s infinite;
    }
    .flag {
      transform-origin: 50% 0;
      animation: sway 3s ease-in-out infinite alternate;
      animation-delay: var(--d);
    }
    .sway {
      transform-origin: 50% 0;
      animation: curtain 4s ease-in-out infinite alternate;
    }
    .scribble {
      transform-origin: 100% 0;
      animation: scribble 0.35s ease-in-out infinite alternate;
    }
    .leg {
      transform-origin: 50% 0;
      animation: step 0.9s ease-in-out infinite alternate;
    }
    .leg-b {
      animation-direction: alternate-reverse;
    }
    .drop {
      animation: drop 2.8s ease-in infinite;
    }
    .steam {
      animation: steam 2.4s ease-out infinite;
    }
    .lean {
      transform-origin: 50% 100%;
      animation: lean 5s ease-in-out infinite alternate;
    }
    .cursor {
      animation: cursor 1s steps(1) infinite;
    }
    .dot {
      animation: dot 1.2s ease-in-out infinite;
      animation-delay: var(--d);
    }
    .minute {
      transform-box: view-box;
      transform-origin: 640px 160px;
      animation: spin 9s linear infinite;
    }
    .shake {
      animation: shake 2.4s infinite;
    }
    .pupil {
      animation: look 1.3s steps(1) infinite;
    }
    .sweat {
      animation: sweat 1.4s ease-in infinite;
      animation-delay: var(--d);
    }
    .alarm {
      transform-origin: 50% 100%;
      animation: alarm 0.8s ease-in-out infinite alternate;
    }
  }

  @keyframes pop {
    from {
      opacity: 0;
      transform: translateY(14px) scale(0.92);
    }
  }
  @keyframes bob {
    to {
      transform: translateY(-5px);
    }
  }
  @keyframes whirl {
    to {
      transform: scaleX(0.35);
    }
  }
  @keyframes blink {
    0%,
    94%,
    100% {
      transform: scaleY(1);
    }
    96% {
      transform: scaleY(0.1);
    }
  }
  @keyframes count {
    to {
      transform: scaleY(0.55);
    }
  }
  @keyframes grow {
    from {
      transform: scaleX(0.7);
    }
  }
  @keyframes draw {
    from {
      stroke-dashoffset: 160;
    }
    60%,
    to {
      stroke-dashoffset: 0;
    }
  }
  @keyframes flicker {
    50% {
      opacity: 0.45;
    }
  }
  @keyframes flash {
    0%,
    80%,
    100% {
      opacity: 0;
    }
    84%,
    92% {
      opacity: 0.85;
    }
    88% {
      opacity: 0.3;
    }
  }
  @keyframes sway {
    from {
      transform: rotate(-5deg);
    }
    to {
      transform: rotate(5deg);
    }
  }
  @keyframes curtain {
    to {
      transform: skewX(2deg);
    }
  }
  @keyframes scribble {
    to {
      transform: rotate(9deg);
    }
  }
  @keyframes step {
    from {
      transform: rotate(-4deg);
    }
    to {
      transform: rotate(4deg);
    }
  }
  @keyframes drop {
    0%,
    20% {
      opacity: 0;
      transform: translateY(-16px);
    }
    35% {
      opacity: 1;
      transform: translateY(-8px);
    }
    75% {
      opacity: 1;
      transform: translateY(6px);
    }
    100% {
      opacity: 0;
      transform: translateY(18px);
    }
  }
  @keyframes steam {
    from {
      opacity: 0.9;
      transform: translateY(0);
    }
    to {
      opacity: 0;
      transform: translateY(-12px);
    }
  }
  @keyframes lean {
    to {
      transform: rotate(-3deg);
    }
  }
  @keyframes cursor {
    50% {
      opacity: 0;
    }
  }
  @keyframes dot {
    0%,
    100% {
      opacity: 0.25;
    }
    50% {
      opacity: 1;
    }
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  @keyframes shake {
    0%,
    30%,
    100% {
      transform: translateX(0);
    }
    5%,
    15%,
    25% {
      transform: translateX(-2px) rotate(-1deg);
    }
    10%,
    20% {
      transform: translateX(2px) rotate(1deg);
    }
  }
  @keyframes look {
    0% {
      transform: translate(-3px, 0);
    }
    33% {
      transform: translate(3px, -1px);
    }
    66% {
      transform: translate(0, 2px);
    }
  }
  @keyframes sweat {
    from {
      opacity: 1;
      transform: translateY(0);
    }
    to {
      opacity: 0;
      transform: translateY(22px);
    }
  }
  @keyframes alarm {
    to {
      transform: scale(1.15);
    }
  }
</style>
