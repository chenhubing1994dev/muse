<script lang="ts">
  /**
   * Desktop window chrome for the frameless Tauri shell.
   * Falls back to a no-op in a plain browser so the web build stays usable.
   */
  import { onMount } from "svelte";

  export let title = "MUSE · 璇玑终端";

  type ResizeDirection =
    | "East"
    | "North"
    | "NorthEast"
    | "NorthWest"
    | "South"
    | "SouthEast"
    | "SouthWest"
    | "West";

  const EDGES: Array<{ dir: ResizeDirection; class: string }> = [
    { dir: "North", class: "edge-n" },
    { dir: "South", class: "edge-s" },
    { dir: "West", class: "edge-w" },
    { dir: "East", class: "edge-e" },
    { dir: "NorthWest", class: "edge-nw" },
    { dir: "NorthEast", class: "edge-ne" },
    { dir: "SouthWest", class: "edge-sw" },
    { dir: "SouthEast", class: "edge-se" },
  ];

  let maximized = false;
  let host: "tauri" | "browser" = "browser";
  let invoke: ((cmd: string) => Promise<unknown>) | null = null;
  let appWindow: {
    minimize: () => Promise<void>;
    toggleMaximize: () => Promise<void>;
    close: () => Promise<void>;
    isMaximized: () => Promise<boolean>;
    startDragging: () => Promise<void>;
    startResizeDragging: (dir: ResizeDirection) => Promise<void>;
    onResized: (fn: () => void) => Promise<() => void>;
  } | null = null;

  onMount(() => {
    let unlisten: (() => void) | null = null;
    let disposed = false;

    void (async () => {
      // The desktop shell injects this API; a plain browser does not.
      const tauri = (window as Window & {
        __TAURI__?: {
          window?: { getCurrentWindow?: () => typeof appWindow };
          core?: { invoke?: (cmd: string) => Promise<unknown> };
        };
      }).__TAURI__;
      const getCurrentWindow = tauri?.window?.getCurrentWindow;
      if (!getCurrentWindow) return;
      const current = getCurrentWindow();
      if (!current) return;
      if (disposed) return;
      appWindow = current;
      invoke = tauri?.core?.invoke ?? null;
      host = "tauri";
      maximized = await current.isMaximized().catch(() => false);
      unlisten = await current
        .onResized(async () => {
          if (!appWindow) return;
          maximized = await appWindow.isMaximized().catch(() => false);
        })
        .catch(() => null);
    })();

    return () => {
      disposed = true;
      unlisten?.();
    };
  });

  function run(action: "minimize" | "maximize" | "close"): void {
    if (!appWindow) return;
    // Maximize goes through the Rust command so it targets the real work area
    // instead of overhanging the screen by the hidden resize frame.
    if (action === "maximize" && invoke) {
      void invoke("win_toggle_maximize")
        .then((value) => {
          maximized = value === true;
        })
        .catch(() => void appWindow?.toggleMaximize());
      return;
    }
    if (action === "minimize") void appWindow.minimize();
    else if (action === "maximize") void appWindow.toggleMaximize();
    else void appWindow.close();
  }

  function startDrag(event: PointerEvent): void {
    // Only the primary button on the drag surface itself, never on controls.
    if (event.button !== 0 || !appWindow) return;
    if ((event.target as HTMLElement | null)?.closest("button")) return;
    void appWindow.startDragging();
  }

  function startResize(event: PointerEvent, dir: ResizeDirection): void {
    if (event.button !== 0 || !appWindow) return;
    event.preventDefault();
    void appWindow.startResizeDragging(dir);
  }
</script>

<svelte:window on:dblclick={(e) => {
  if ((e.target as HTMLElement | null)?.closest(".tb-drag")) run("maximize");
}} />

{#if host === "tauri"}
  <div class="frame" aria-hidden="true">
    {#each EDGES as edge (edge.dir)}
      <span class={`edge ${edge.class}`} role="presentation" onpointerdown={(e) => startResize(e, edge.dir)}></span>
    {/each}
  </div>
{/if}

<div class="tb-drag" role="presentation" onpointerdown={startDrag} aria-label={title}></div>

<div class="tb-window">
  {#if host === "tauri"}
    <button class="win-btn" type="button" onclick={() => run("minimize")} title="最小化" aria-label="最小化窗口">
      <svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6h7" /></svg>
    </button>
    <button class="win-btn" type="button" onclick={() => run("maximize")} title={maximized ? "还原" : "最大化"} aria-label={maximized ? "还原窗口" : "最大化窗口"}>
      {#if maximized}
        <svg viewBox="0 0 12 12" aria-hidden="true"><rect x="2.4" y="4.2" width="5.2" height="5.2" /><path d="M4.4 4.2V2.6h5v5H7.8" /></svg>
      {:else}
        <svg viewBox="0 0 12 12" aria-hidden="true"><rect x="2.6" y="2.6" width="6.8" height="6.8" /></svg>
      {/if}
    </button>
    <button class="win-btn is-close" type="button" onclick={() => run("close")} title="关闭" aria-label="关闭窗口">
      <svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 3l6 6M9 3l-6 6" /></svg>
    </button>
  {:else}
    <span class="win-btn is-inert" aria-hidden="true"><svg viewBox="0 0 12 12"><path d="M2.5 6h7" /></svg></span>
    <span class="win-btn is-inert" aria-hidden="true"><svg viewBox="0 0 12 12"><rect x="2.6" y="2.6" width="6.8" height="6.8" /></svg></span>
    <span class="win-btn is-inert" aria-hidden="true"><svg viewBox="0 0 12 12"><path d="M3 3l6 6M9 3l-6 6" /></svg></span>
  {/if}
</div>
