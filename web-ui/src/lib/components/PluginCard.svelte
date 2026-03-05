<!--
  PluginCard.svelte — Reusable card for displaying a plugin's info.

  Used by both the Installed and Available tabs in PluginsView.
  Shows name, version, author, badges (type, state, error), description,
  error/warning banners, and action buttons passed via snippets.
-->
<script lang="ts">
  import type { Snippet } from "svelte";
  import type { PluginInfo } from "$lib/api";
  import { AlertTriangle } from "lucide-svelte";

  interface Props {
    plugin: PluginInfo;
    /** Optional extra badges rendered after the default type/state badges. */
    extraBadges?: Snippet;
    /** Action buttons rendered at the bottom of the card. */
    actions?: Snippet;
    /** If true, hide the default type + state badges (used by Available tab). */
    hidePrimaryBadges?: boolean;
  }

  let {
    plugin,
    extraBadges,
    actions,
    hidePrimaryBadges = false,
  }: Props = $props();
</script>

<article class="p-4 rounded-xl bg-surface-0 border border-border space-y-3">
  <!-- Header: name, version, author + badges -->
  <div class="flex items-start justify-between gap-3">
    <div class="min-w-0">
      <h3 class="text-text font-semibold truncate">{plugin.name}</h3>
      <p class="text-xs text-overlay-1">
        {plugin.version} · {plugin.author || "Unknown author"}
      </p>
    </div>
    <div class="flex gap-1 shrink-0">
      {#if !hidePrimaryBadges}
        <span
          class="px-2 py-1 rounded text-xs {plugin.type === 'core'
            ? 'bg-blue-500/20 text-blue-300'
            : 'bg-emerald-500/20 text-emerald-300'}"
        >
          {plugin.type}
        </span>
        {#if plugin.error}
          <span class="px-2 py-1 rounded text-xs bg-red-500/20 text-red-300">
            error
          </span>
        {:else}
          <span
            class="px-2 py-1 rounded text-xs {plugin.state === 'enabled'
              ? 'bg-emerald-500/20 text-emerald-300'
              : 'bg-zinc-500/20 text-zinc-300'}"
          >
            {plugin.state}
          </span>
        {/if}
      {/if}
      {#if extraBadges}
        {@render extraBadges()}
      {/if}
    </div>
  </div>

  <!-- Description -->
  <p class="text-sm text-overlay-1">{plugin.description || "No description."}</p>

  <!-- Error / warning banners -->
  {#if plugin.error}
    <div
      class="p-2.5 rounded-lg border border-red-500/40 bg-red-500/10 text-red-200 flex items-start gap-2.5"
    >
      <AlertTriangle size={16} class="mt-0.5 shrink-0" />
      <p class="text-xs leading-relaxed">{plugin.error}</p>
    </div>
  {:else if plugin.state === "enabled" && !plugin.started}
    <div
      class="p-2.5 rounded-lg border border-yellow-500/40 bg-yellow-500/10 text-yellow-200 flex items-start gap-2.5"
    >
      <AlertTriangle size={16} class="mt-0.5 shrink-0" />
      <p class="text-xs">
        Plugin is enabled but not running. A server restart may be required.
      </p>
    </div>
  {/if}

  <!-- Action buttons (provided by parent) -->
  {#if actions}
    <div class="flex flex-wrap gap-2">
      {@render actions()}
    </div>
  {/if}
</article>
