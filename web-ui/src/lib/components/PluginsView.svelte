<!--
  PluginsView.svelte — Plugin management page.

  Slim orchestrator that owns the shared state (plugin lists, selected plugin,
  busy flags) and delegates rendering to three focused sub-components:

    - PluginsInstalled  — grid of installed plugin cards
    - PluginsAvailable  — repository browser with search/filter
    - PluginSettings    — per-plugin settings form

  All API calls and cross-tab coordination live here; the child components
  receive data and emit callbacks.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import {
    api,
    type PluginInfo,
    type PluginSettingDefinition,
    type RepositoryPlugin,
  } from "$lib/api";
  import { toastStore } from "$lib/stores/toast.svelte";
  import { PlugZap, RefreshCw, AlertTriangle } from "lucide-svelte";

  import PluginsInstalled from "./PluginsInstalled.svelte";
  import PluginsAvailable from "./PluginsAvailable.svelte";
  import PluginSettings from "./PluginSettings.svelte";

  // ---------------------------------------------------------------------------
  // Shared state
  // ---------------------------------------------------------------------------

  type Tab = "installed" | "available" | "settings";

  let activeTab = $state<Tab>("installed");
  let loadingInstalled = $state(false);
  let loadingAvailable = $state(false);
  let savingSettings = $state(false);
  let busyPlugin = $state<string | null>(null);

  let plugins = $state<PluginInfo[]>([]);
  let repositoryPlugins = $state<RepositoryPlugin[]>([]);
  let restartRequired = $state(false);

  // Settings tab state
  let selectedPlugin = $state<string | null>(null);
  let definitions = $state<PluginSettingDefinition[]>([]);
  let values = $state<Record<string, unknown>>({});

  const selectedPluginInfo = $derived(() =>
    selectedPlugin
      ? plugins.find((p) => p.name === selectedPlugin) ?? null
      : null,
  );

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  function sortDefinitions(items: PluginSettingDefinition[]): PluginSettingDefinition[] {
    return [...items].sort((a, b) => (a.order - b.order) || a.key.localeCompare(b.key));
  }

  // ---------------------------------------------------------------------------
  // Data loading
  // ---------------------------------------------------------------------------

  async function loadPlugins() {
    loadingInstalled = true;
    try {
      const response = await api.getPlugins();
      plugins = response.plugins;
      restartRequired = response.restart_required;
    } catch (err) {
      toastStore.error("Failed to load plugins", {
        detail: (err as Error).message,
      });
    } finally {
      loadingInstalled = false;
    }
  }

  async function loadRepository(forceRefresh = false) {
    loadingAvailable = true;
    try {
      const response = await api.getRepository(forceRefresh);
      repositoryPlugins = response.plugins;
    } catch (err) {
      toastStore.error("Failed to load repository", {
        detail: (err as Error).message,
      });
    } finally {
      loadingAvailable = false;
    }
  }

  async function refreshAll() {
    await Promise.all([loadPlugins(), loadRepository(true)]);
  }

  // ---------------------------------------------------------------------------
  // Installed tab actions
  // ---------------------------------------------------------------------------

  async function togglePlugin(plugin: PluginInfo) {
    busyPlugin = plugin.name;
    try {
      if (plugin.state === "enabled") {
        await api.disablePlugin(plugin.name);
        toastStore.info(`Plugin disabled: ${plugin.name}`);
      } else {
        await api.enablePlugin(plugin.name);
        toastStore.success(`Plugin enabled: ${plugin.name}`);
      }
      await loadPlugins();
    } catch (err) {
      toastStore.error(`Failed to update plugin state: ${plugin.name}`, {
        detail: (err as Error).message,
      });
    } finally {
      busyPlugin = null;
    }
  }

  async function uninstallPlugin(plugin: PluginInfo) {
    if (!plugin.can_uninstall) return;
    if (!confirm(`Uninstall plugin '${plugin.name}'?`)) return;

    busyPlugin = plugin.name;
    try {
      await api.uninstallPlugin(plugin.name);
      toastStore.success(`Plugin uninstalled: ${plugin.name}`);
      await Promise.all([loadPlugins(), loadRepository(true)]);
      if (selectedPlugin === plugin.name) {
        selectedPlugin = null;
        definitions = [];
        values = {};
        activeTab = "installed";
      }
    } catch (err) {
      toastStore.error(`Failed to uninstall plugin: ${plugin.name}`, {
        detail: (err as Error).message,
      });
    } finally {
      busyPlugin = null;
    }
  }

  // ---------------------------------------------------------------------------
  // Available tab actions
  // ---------------------------------------------------------------------------

  async function installFromRepository(pluginName: string) {
    busyPlugin = pluginName;
    try {
      await api.installFromRepository(pluginName);
      toastStore.success(`Plugin installed: ${pluginName}`);
      await Promise.all([loadPlugins(), loadRepository(true)]);
    } catch (err) {
      toastStore.error(`Failed to install plugin: ${pluginName}`, {
        detail: (err as Error).message,
      });
    } finally {
      busyPlugin = null;
    }
  }

  // ---------------------------------------------------------------------------
  // Settings tab actions
  // ---------------------------------------------------------------------------

  async function openSettings(pluginName: string) {
    try {
      const response = await api.getPluginSettings(pluginName);
      selectedPlugin = pluginName;
      definitions = sortDefinitions(response.definitions);
      values = { ...response.values };
      activeTab = "settings";
    } catch (err) {
      toastStore.error(`Failed to load settings for ${pluginName}`, {
        detail: (err as Error).message,
      });
    }
  }

  async function saveSettings() {
    if (!selectedPlugin) return;
    savingSettings = true;
    try {
      const response = await api.updatePluginSettings(selectedPlugin, values);
      definitions = sortDefinitions(response.definitions);
      values = { ...response.values };
      toastStore.success(`Settings saved: ${selectedPlugin}`);
      await loadPlugins();
    } catch (err) {
      toastStore.error(`Failed to save settings: ${selectedPlugin}`, {
        detail: (err as Error).message,
      });
    } finally {
      savingSettings = false;
    }
  }

  function resetDefaults() {
    const next: Record<string, unknown> = {};
    for (const definition of definitions) {
      next[definition.key] = definition.default;
    }
    values = next;
  }

  // ---------------------------------------------------------------------------
  // Lifecycle
  // ---------------------------------------------------------------------------

  onMount(async () => {
    await Promise.all([loadPlugins(), loadRepository()]);
  });
</script>

<div class="p-6 space-y-6">
  <!-- Page header -->
  <div class="flex items-center justify-between gap-4">
    <div>
      <h2 class="text-2xl font-semibold text-text">Plugins</h2>
      <p class="text-sm text-overlay-1">
        Manage installed plugins, repository packages, and per-plugin settings.
      </p>
    </div>
    <button
      class="px-3 py-2 rounded-lg bg-surface-0 hover:bg-surface-1 text-overlay-1 hover:text-text transition-colors flex items-center gap-2"
      onclick={refreshAll}
      disabled={loadingInstalled || loadingAvailable}
    >
      <RefreshCw
        size={16}
        class={(loadingInstalled || loadingAvailable) ? "animate-spin" : ""}
      />
      Refresh
    </button>
  </div>

  <!-- Restart-required banner -->
  {#if restartRequired}
    <div
      class="p-3 rounded-lg border border-yellow-500/40 bg-yellow-500/10 text-yellow-200 flex items-start gap-3"
    >
      <AlertTriangle size={18} class="mt-0.5 shrink-0" />
      <p class="text-sm">
        Plugin changes require a server restart to take full effect.
      </p>
    </div>
  {/if}

  <!-- Tab bar -->
  <div class="flex flex-wrap gap-2">
    <button
      class="px-3 py-2 rounded-lg text-sm transition-colors {activeTab === 'installed'
        ? 'bg-accent text-mantle font-semibold'
        : 'bg-surface-0 text-overlay-1 hover:text-text'}"
      onclick={() => (activeTab = "installed")}
    >
      Installed
    </button>
    <button
      class="px-3 py-2 rounded-lg text-sm transition-colors {activeTab === 'available'
        ? 'bg-accent text-mantle font-semibold'
        : 'bg-surface-0 text-overlay-1 hover:text-text'}"
      onclick={() => (activeTab = "available")}
    >
      Available
    </button>
    <button
      class="px-3 py-2 rounded-lg text-sm transition-colors {activeTab === 'settings'
        ? 'bg-accent text-mantle font-semibold'
        : 'bg-surface-0 text-overlay-1 hover:text-text'}"
      onclick={() => (activeTab = "settings")}
      disabled={!selectedPlugin}
    >
      Settings
    </button>
  </div>

  <!-- Tab content -->
  {#if activeTab === "installed"}
    <PluginsInstalled
      {plugins}
      loading={loadingInstalled}
      {busyPlugin}
      onToggle={togglePlugin}
      onSettings={openSettings}
      onUninstall={uninstallPlugin}
    />
  {/if}

  {#if activeTab === "available"}
    <PluginsAvailable
      {repositoryPlugins}
      loading={loadingAvailable}
      {busyPlugin}
      onInstall={installFromRepository}
      onRefresh={() => loadRepository(true)}
    />
  {/if}

  {#if activeTab === "settings"}
    <PluginSettings
      {selectedPlugin}
      selectedPluginInfo={selectedPluginInfo()}
      {definitions}
      {values}
      saving={savingSettings}
      onSave={saveSettings}
      onResetDefaults={resetDefaults}
      onValuesChange={(v) => (values = v)}
    />
  {/if}
</div>
