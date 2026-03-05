<!--
  PluginsInstalled.svelte — "Installed" tab for the Plugins page.

  Displays a responsive grid of PluginCards for every discovered plugin,
  with Enable/Disable, Settings, and Uninstall action buttons.
-->
<script lang="ts">
  import type { PluginInfo } from "$lib/api";
  import PluginCard from "./PluginCard.svelte";
  import { Power, PowerOff, Settings2, Trash2 } from "lucide-svelte";

  interface Props {
    plugins: PluginInfo[];
    loading: boolean;
    busyPlugin: string | null;
    onToggle: (plugin: PluginInfo) => void;
    onSettings: (pluginName: string) => void;
    onUninstall: (plugin: PluginInfo) => void;
  }

  let {
    plugins,
    loading,
    busyPlugin,
    onToggle,
    onSettings,
    onUninstall,
  }: Props = $props();
</script>

<div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
  {#if loading}
    <div class="col-span-full p-8 text-center text-overlay-1">Loading plugins…</div>
  {:else if plugins.length === 0}
    <div class="col-span-full p-8 rounded-xl bg-surface-0 text-overlay-1 text-center">
      No plugins discovered.
    </div>
  {:else}
    {#each plugins as plugin (plugin.name)}
      <PluginCard {plugin}>
        {#snippet actions()}
          <button
            class="px-3 py-1.5 rounded text-sm bg-surface-1 hover:bg-surface-2 text-text transition-colors flex items-center gap-2"
            onclick={() => onToggle(plugin)}
            disabled={busyPlugin === plugin.name}
          >
            {#if plugin.state === "enabled"}
              <PowerOff size={14} />
              Disable
            {:else}
              <Power size={14} />
              Enable
            {/if}
          </button>

          {#if plugin.has_settings}
            <button
              class="px-3 py-1.5 rounded text-sm bg-surface-1 hover:bg-surface-2 text-text transition-colors flex items-center gap-2"
              onclick={() => onSettings(plugin.name)}
              disabled={busyPlugin === plugin.name}
            >
              <Settings2 size={14} />
              Settings
            </button>
          {/if}

          {#if plugin.can_uninstall}
            <button
              class="px-3 py-1.5 rounded text-sm bg-red-500/20 hover:bg-red-500/30 text-red-200 transition-colors flex items-center gap-2"
              onclick={() => onUninstall(plugin)}
              disabled={busyPlugin === plugin.name}
            >
              <Trash2 size={14} />
              Uninstall
            </button>
          {/if}
        {/snippet}
      </PluginCard>
    {/each}
  {/if}
</div>
