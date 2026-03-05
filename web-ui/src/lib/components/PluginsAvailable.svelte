<!--
  PluginsAvailable.svelte — "Available" tab for the Plugins page.

  Displays repository plugins with search, category filtering,
  and Install/Update action buttons. Uses PluginCard for consistent
  presentation with the Installed tab.
-->
<script lang="ts">
  import type { PluginInfo, RepositoryPlugin } from "$lib/api";
  import { Download, RefreshCw, Search } from "lucide-svelte";

  interface Props {
    repositoryPlugins: RepositoryPlugin[];
    loading: boolean;
    busyPlugin: string | null;
    onInstall: (pluginName: string) => void;
    onRefresh: () => void;
  }

  let {
    repositoryPlugins,
    loading,
    busyPlugin,
    onInstall,
    onRefresh,
  }: Props = $props();

  let categoryFilter = $state("all");
  let searchText = $state("");

  const categories = $derived(() => {
    const categorySet = new Set<string>();
    for (const plugin of repositoryPlugins) {
      if (plugin.category) categorySet.add(plugin.category);
    }
    return ["all", ...Array.from(categorySet).sort((a, b) => a.localeCompare(b))];
  });

  const filteredPlugins = $derived(() => {
    const q = searchText.trim().toLowerCase();
    return repositoryPlugins.filter((plugin) => {
      if (categoryFilter !== "all" && plugin.category !== categoryFilter) {
        return false;
      }
      if (!q) return true;
      return (
        plugin.name.toLowerCase().includes(q) ||
        plugin.description.toLowerCase().includes(q) ||
        plugin.tags.some((tag) => tag.toLowerCase().includes(q))
      );
    });
  });
</script>

<!-- Search + Filter toolbar -->
<div class="flex flex-wrap gap-3 items-center">
  <div class="relative">
    <Search size={16} class="absolute left-2 top-2.5 text-overlay-1" />
    <input
      class="pl-8 pr-3 py-2 rounded-lg bg-surface-0 border border-border text-sm text-text placeholder:text-overlay-1"
      placeholder="Search plugins..."
      bind:value={searchText}
    />
  </div>
  <select
    class="px-3 py-2 rounded-lg bg-surface-0 border border-border text-sm text-text"
    bind:value={categoryFilter}
  >
    {#each categories() as category}
      <option value={category}>{category}</option>
    {/each}
  </select>
  <button
    class="px-3 py-2 rounded-lg bg-surface-0 hover:bg-surface-1 text-overlay-1 hover:text-text transition-colors flex items-center gap-2"
    onclick={onRefresh}
    disabled={loading}
  >
    <RefreshCw size={14} class={loading ? "animate-spin" : ""} />
    Refresh
  </button>
</div>

<!-- Plugin grid -->
<div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
  {#if loading}
    <div class="col-span-full p-8 text-center text-overlay-1">Loading repository…</div>
  {:else if filteredPlugins().length === 0}
    <div class="col-span-full p-8 rounded-xl bg-surface-0 text-overlay-1 text-center">
      No matching repository plugins.
    </div>
  {:else}
    {#each filteredPlugins() as plugin (plugin.name)}
      <article class="p-4 rounded-xl bg-surface-0 border border-border space-y-3">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <h3 class="text-text font-semibold truncate">{plugin.name}</h3>
            <p class="text-xs text-overlay-1">
              {plugin.version} · {plugin.author || "Unknown author"}
            </p>
          </div>
          <div class="flex gap-1 shrink-0">
            {#if plugin.is_core}
              <span class="px-2 py-1 rounded text-xs bg-blue-500/20 text-blue-300">core</span>
            {/if}
            {#if !plugin.compatible}
              <span class="px-2 py-1 rounded text-xs bg-red-500/20 text-red-300">
                incompatible
              </span>
            {/if}
          </div>
        </div>

        <p class="text-sm text-overlay-1">{plugin.description || "No description."}</p>

        {#if plugin.tags.length > 0}
          <div class="flex flex-wrap gap-1">
            {#each plugin.tags as tag}
              <span class="px-2 py-0.5 rounded text-xs bg-surface-1 text-overlay-1">{tag}</span>
            {/each}
          </div>
        {/if}

        {#if !plugin.compatible && plugin.incompatible_reason}
          <p class="text-xs text-red-400">{plugin.incompatible_reason}</p>
        {/if}

        <div class="flex flex-wrap gap-2">
          <button
            class="px-3 py-1.5 rounded text-sm bg-surface-1 hover:bg-surface-2 text-text transition-colors flex items-center gap-2 disabled:opacity-50"
            onclick={() => onInstall(plugin.name)}
            disabled={busyPlugin === plugin.name ||
              (!plugin.can_install && !plugin.can_update)}
          >
            <Download size={14} />
            {plugin.can_update
              ? "Update"
              : plugin.can_install
                ? "Install"
                : !plugin.compatible
                  ? "Incompatible"
                  : "Installed"}
          </button>
          {#if plugin.installed_version}
            <span class="px-2 py-1 rounded text-xs bg-zinc-500/20 text-zinc-300">
              installed: {plugin.installed_version}
            </span>
          {/if}
        </div>
      </article>
    {/each}
  {/if}
</div>
