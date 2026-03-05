<script lang="ts">
  import type { Snippet } from "svelte";
  import DynamicIcon from "$lib/components/DynamicIcon.svelte";
  import PluginRenderer from "../PluginRenderer.svelte";

  let {
    tabs = [],
  }: {
    tabs?: Array<{
      label: string;
      icon?: string;
      children?: Array<any>;
    }>;
  } = $props();

  let activeIndex = $state(0);
</script>

{#if tabs.length > 0}
  <div class="flex flex-col gap-0">
    <!-- Tab buttons -->
    <div class="flex border-b border-border">
      {#each tabs as tab, i}
        <button
          class="relative flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors
            {i === activeIndex
            ? 'text-accent'
            : 'text-overlay-1 hover:text-text'}"
          onclick={() => (activeIndex = i)}
        >
          {#if tab.icon}
            <DynamicIcon name={tab.icon} size={16} />
          {/if}
          {tab.label}

          <!-- Active indicator -->
          {#if i === activeIndex}
            <span
              class="absolute bottom-0 left-0 right-0 h-0.5 bg-accent rounded-t-full"
            ></span>
          {/if}
        </button>
      {/each}
    </div>

    <!-- Active tab content -->
    {#if tabs[activeIndex]?.children}
      <div class="pt-4 space-y-4">
        {#each tabs[activeIndex].children as child}
          <PluginRenderer component={child} />
        {/each}
      </div>
    {/if}
  </div>
{/if}
