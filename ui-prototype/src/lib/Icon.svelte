<script lang="ts">
  /** Line-art glyph set for the 璇玑 shell. One stroke language on a 24px grid. */
  export let name: string = "core";
  export let size: number | string = 20;

  const PATHS: Record<string, string[]> = {
    core: ["M12 3.4 14.9 9l6.1.9-4.4 4.3 1 6.1L12 17.4 6.4 20.3l1-6.1L3 9.9 9.1 9z"],
    spark: [
      "M12 3v4.2M12 16.8V21M3 12h4.2M16.8 12H21",
      "M6.9 6.9l2.6 2.6M14.5 14.5l2.6 2.6M17.1 6.9l-2.6 2.6M9.5 14.5l-2.6 2.6",
      "M12 9.6a2.4 2.4 0 1 0 0 4.8 2.4 2.4 0 0 0 0-4.8z",
    ],
    agents: [
      "M12 3.6 5 7.4v9.2l7 3.8 7-3.8V7.4z",
      "M12 8.2v7.6M8.3 10.2v3.6M15.7 10.2v3.6",
    ],
    tasks: ["M4 6.5h10M4 12h16M4 17.5h7", "M19.2 5.1 20.6 6.5 18.4 8.7"],
    knowledge: [
      "M12 3.2v3M12 17.8v3M4.3 12h3M16.7 12h3",
      "M12 8.1a3.9 3.9 0 1 0 0 7.8 3.9 3.9 0 0 0 0-7.8z",
      "M6.4 6.4l2.1 2.1M15.5 15.5l2.1 2.1M17.6 6.4l-2.1 2.1M8.5 15.5l-2.1 2.1",
    ],
    studio: ["M4 6.6h11.4v10.8H4z", "M15.4 11.2 20 8.4v7.2l-4.6-2.8z", "M7.4 9.6h3.6"],
    flame: [
      "M12 3.2c3.2 3.4 4.9 6 4.9 8.6a4.9 4.9 0 0 1-9.8 0c0-2.6 1.7-5.2 4.9-8.6z",
      "M12 12.2c1 1 1.6 1.8 1.6 2.6a1.6 1.6 0 1 1-3.2 0c0-.8.6-1.6 1.6-2.6z",
    ],
    memory: ["M6.2 5h11.6v14H6.2z", "M9.2 8.6h5.6M9.2 12h5.6M9.2 15.4h3.4"],
    evolve: ["M20 12a8 8 0 1 1-2.6-5.9", "M20 4.2v4.4h-4.4", "M12 8.4v4l2.6 1.6"],
    refresh: [
      "M20 6.4v4.2h-4.2",
      "M4 17.6v-4.2h4.2",
      "M19.4 10.6A7.6 7.6 0 0 0 6 8.2M4.6 13.4A7.6 7.6 0 0 0 18 15.8",
    ],
    search: ["M10.8 4.6a6.2 6.2 0 1 0 0 12.4 6.2 6.2 0 0 0 0-12.4z", "M15.5 15.5 20 20"],
    book: [
      "M4.6 5.2h6.1A2.3 2.3 0 0 1 13 7.5v11.3a2 2 0 0 0-2-2H4.6z",
      "M19.4 5.2h-4.1a2.3 2.3 0 0 0-2.3 2.3",
    ],
    play: ["M9.4 7.4 16 12l-6.6 4.6z"],
    lock: ["M6.6 10.6h10.8V20H6.6z", "M9 10.6V8.2a3 3 0 0 1 6 0v2.4"],
    leave: ["M14.6 4.6H5.4v14.8h9.2", "M12.8 12h7.4M17.4 8.8 20.6 12l-3.2 3.2"],
    plus: ["M12 5.4v13.2M5.4 12h13.2"],
    clock: ["M12 4.4a7.6 7.6 0 1 0 0 15.2 7.6 7.6 0 0 0 0-15.2z", "M12 8v4.4l3 1.8"],
    shield: ["M12 3.4 19 6v6.2c0 3.9-3 7-7 8.4-4-1.4-7-4.5-7-8.4V6z", "M9.2 11.8 11.4 14l3.6-3.8"],
    layers: ["M12 3.6 4 8l8 4.4L20 8z", "M4 12.4l8 4.4 8-4.4M4 16.6l8 4.4 8-4.4"],
    compass: [
      "M12 3.6a8.4 8.4 0 1 0 0 16.8 8.4 8.4 0 0 0 0-16.8z",
      "M15.2 8.8l-1.9 5.2-5.2 1.9 1.9-5.2z",
    ],
  };

  $: paths = PATHS[name] ?? PATHS.core;
</script>

<svg
  viewBox="0 0 24 24"
  width={size}
  height={size}
  aria-hidden="true"
  focusable="false"
  fill="none"
  stroke="currentColor"
  stroke-width="1.35"
  stroke-linecap="round"
  stroke-linejoin="round"
>
  {#each paths as d (d)}
    <path {d} />
  {/each}
</svg>
