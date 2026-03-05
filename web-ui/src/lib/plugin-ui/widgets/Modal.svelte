<script lang="ts">
  import type { Snippet } from "svelte";
  import PluginRenderer from "../PluginRenderer.svelte";

  let {
    title = "",
    trigger_label = "Open",
    trigger_style = "secondary",
    trigger_icon = null,
    size = "md",
    children,
  }: {
    title?: string;
    trigger_label?: string;
    trigger_style?: string;
    trigger_icon?: string | null;
    size?: string;
    children?: Snippet;
  } = $props();

  let open = $state(false);

  const triggerStyleMap: Record<string, string> = {
    primary: "bg-accent text-crust hover:opacity-80",
    secondary: "bg-surface-1 text-text hover:bg-surface-2",
    danger: "bg-error text-crust hover:opacity-80",
  };

  const sizeMap: Record<string, string> = {
    sm: "max-w-sm",
    md: "max-w-lg",
    lg: "max-w-2xl",
    xl: "max-w-4xl",
  };

  const triggerClasses = $derived(
    triggerStyleMap[trigger_style] ?? triggerStyleMap.secondary,
  );
  const sizeClass = $derived(sizeMap[size] ?? sizeMap.md);

  function openModal() {
    open = true;
  }

  function closeModal() {
    open = false;
  }

  function handleBackdropClick(e: MouseEvent) {
    if (e.target === e.currentTarget) {
      closeModal();
    }
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === "Escape") {
      closeModal();
    }
  }
</script>

<!-- Trigger button -->
<button
  class="rounded-lg px-4 py-2 text-sm font-medium transition-all {triggerClasses}"
  onclick={openModal}
>
  {trigger_label}
</button>

<!-- Modal overlay -->
{#if open}
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <div
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
    role="dialog"
    aria-modal="true"
    aria-label={title}
    tabindex="-1"
    onclick={handleBackdropClick}
    onkeydown={handleKeydown}
  >
    <div
      class="relative w-full {sizeClass} mx-4 max-h-[85vh] flex flex-col rounded-xl border border-border bg-base shadow-2xl"
    >
      <!-- Header -->
      <div
        class="flex items-center justify-between border-b border-border px-6 py-4"
      >
        <h2 class="text-lg font-semibold text-text">{title}</h2>
        <button
          class="rounded-lg p-1.5 text-overlay-1 transition-colors hover:bg-surface-1 hover:text-text"
          onclick={closeModal}
          aria-label="Close dialog"
        >
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <path d="M18 6 6 18" />
            <path d="m6 6 12 12" />
          </svg>
        </button>
      </div>

      <!-- Body -->
      <div class="overflow-y-auto px-6 py-4 space-y-4">
        {#if children}
          {@render children()}
        {/if}
      </div>
    </div>
  </div>
{/if}
