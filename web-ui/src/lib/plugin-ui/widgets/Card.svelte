<script lang="ts">
  import type { Snippet } from "svelte";

  let {
    title = "",
    collapsible = false,
    collapsed = false,
    children,
  }: {
    title?: string;
    collapsible?: boolean;
    collapsed?: boolean;
    children?: Snippet;
  } = $props();

  // Intentionally capture the initial `collapsed` prop as local state.
  // svelte-ignore state_referenced_locally
  let isCollapsed = $state(collapsed);

  function toggle() {
    if (collapsible) {
      isCollapsed = !isCollapsed;
    }
  }
</script>

<div class="rounded-xl border border-border bg-mantle overflow-hidden">
  {#if title}
    {#if collapsible}
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <button
        type="button"
        class="flex w-full items-center justify-between px-4 py-3 text-left
          text-sm font-semibold text-subtext-1 uppercase tracking-wider
          hover:bg-surface-0/50 transition-colors select-none"
        onclick={toggle}
        aria-expanded={!isCollapsed}
      >
        <span>{title}</span>
        <svg
          class="h-4 w-4 text-overlay-1 transition-transform duration-200
            {isCollapsed ? '-rotate-90' : 'rotate-0'}"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>
    {:else}
      <h3
        class="px-4 py-3 text-sm font-semibold text-subtext-1 uppercase tracking-wider"
      >
        {title}
      </h3>
    {/if}
  {/if}

  {#if !collapsible || !isCollapsed}
    <div
      class="px-4 pb-4 space-y-4"
      class:pt-0={!!title}
      class:pt-4={!title}
    >
      {#if children}
        {@render children()}
      {/if}
    </div>
  {/if}
</div>
