<script lang="ts">
  import { onMount } from "svelte";
  import {
    api,
    type PluginInfo,
    type PluginSettingDefinition,
    type RepositoryPlugin,
  } from "$lib/api";
  import { toastStore } from "$lib/stores/toast.svelte";
  import {
    PlugZap,
    RefreshCw,
    Settings2,
    Download,
    Power,
    PowerOff,
    Trash2,
    AlertTriangle,
    Search,
  } from "lucide-svelte";

  type Tab = "installed" | "available" | "settings";

  let activeTab = $state<Tab>("installed");
  let loadingInstalled = $state(false);
  let loadingAvailable = $state(false);
  let savingSettings = $state(false);
  let busyPlugin = $state<string | null>(null);

  let plugins = $state<PluginInfo[]>([]);
  let repositoryPlugins = $state<RepositoryPlugin[]>([]);
  let restartRequired = $state(false);

  let selectedPlugin = $state<string | null>(null);
  let definitions = $state<PluginSettingDefinition[]>([]);
  let values = $state<Record<string, unknown>>({});

  let categoryFilter = $state("all");
  let searchText = $state("");

  const categories = $derived(() => {
    const categorySet = new Set<string>();
    for (const plugin of repositoryPlugins) {
      if (plugin.category) categorySet.add(plugin.category);
    }
    return ["all", ...Array.from(categorySet).sort((a, b) => a.localeCompare(b))];
  });

  const filteredRepository = $derived(() => {
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

  const selectedPluginInfo = $derived(() =>
    selectedPlugin
      ? plugins.find((plugin) => plugin.name === selectedPlugin) ?? null
      : null,
  );

  function sortDefinitions(items: PluginSettingDefinition[]): PluginSettingDefinition[] {
    return [...items].sort((a, b) => (a.order - b.order) || a.key.localeCompare(b.key));
  }

  function parseInputValue(definition: PluginSettingDefinition, raw: unknown): unknown {
    if (definition.type === "int") {
      const n = Number.parseInt(String(raw), 10);
      return Number.isNaN(n) ? 0 : n;
    }
    if (definition.type === "float") {
      const n = Number.parseFloat(String(raw));
      return Number.isNaN(n) ? 0 : n;
    }
    if (definition.type === "bool") {
      return Boolean(raw);
    }
    return String(raw ?? "");
  }

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

  async function loadSettings(pluginName: string) {
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

  onMount(async () => {
    await Promise.all([loadPlugins(), loadRepository()]);
  });
</script>

<div class="p-6 space-y-6">
  <div class="flex items-center justify-between gap-4">
    <div>
      <h2 class="text-2xl font-semibold text-text">Plugins</h2>
      <p class="text-sm text-overlay-1">
        Manage installed plugins, repository packages, and per-plugin settings.
      </p>
    </div>
    <button
      class="px-3 py-2 rounded-lg bg-surface-0 hover:bg-surface-1 text-overlay-1 hover:text-text transition-colors flex items-center gap-2"
      onclick={() => Promise.all([loadPlugins(), loadRepository(true)])}
      disabled={loadingInstalled || loadingAvailable}
    >
      <RefreshCw size={16} class={(loadingInstalled || loadingAvailable) ? "animate-spin" : ""} />
      Refresh
    </button>
  </div>

  {#if restartRequired}
    <div class="p-3 rounded-lg border border-yellow-500/40 bg-yellow-500/10 text-yellow-200 flex items-start gap-3">
      <AlertTriangle size={18} class="mt-0.5 shrink-0" />
      <p class="text-sm">Plugin changes require a server restart to take full effect.</p>
    </div>
  {/if}

  <div class="flex flex-wrap gap-2">
    <button
      class="px-3 py-2 rounded-lg text-sm transition-colors {activeTab === 'installed' ? 'bg-accent text-mantle font-semibold' : 'bg-surface-0 text-overlay-1 hover:text-text'}"
      onclick={() => (activeTab = "installed")}
    >
      Installed
    </button>
    <button
      class="px-3 py-2 rounded-lg text-sm transition-colors {activeTab === 'available' ? 'bg-accent text-mantle font-semibold' : 'bg-surface-0 text-overlay-1 hover:text-text'}"
      onclick={() => (activeTab = "available")}
    >
      Available
    </button>
    <button
      class="px-3 py-2 rounded-lg text-sm transition-colors {activeTab === 'settings' ? 'bg-accent text-mantle font-semibold' : 'bg-surface-0 text-overlay-1 hover:text-text'}"
      onclick={() => (activeTab = "settings")}
      disabled={!selectedPlugin}
    >
      Settings
    </button>
  </div>

  {#if activeTab === "installed"}
    <div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
      {#if loadingInstalled}
        <div class="col-span-full p-8 text-center text-overlay-1">Loading plugins…</div>
      {:else if plugins.length === 0}
        <div class="col-span-full p-8 rounded-xl bg-surface-0 text-overlay-1 text-center">
          No plugins discovered.
        </div>
      {:else}
        {#each plugins as plugin}
          <article class="p-4 rounded-xl bg-surface-0 border border-border space-y-3">
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <h3 class="text-text font-semibold truncate">{plugin.name}</h3>
                <p class="text-xs text-overlay-1">{plugin.version} · {plugin.author || "Unknown author"}</p>
              </div>
              <div class="flex gap-1 shrink-0">
                <span class="px-2 py-1 rounded text-xs {plugin.type === 'core' ? 'bg-blue-500/20 text-blue-300' : 'bg-emerald-500/20 text-emerald-300'}">
                  {plugin.type}
                </span>
                <span class="px-2 py-1 rounded text-xs {plugin.state === 'enabled' ? 'bg-emerald-500/20 text-emerald-300' : 'bg-zinc-500/20 text-zinc-300'}">
                  {plugin.state}
                </span>
              </div>
            </div>

            <p class="text-sm text-overlay-1">{plugin.description || "No description."}</p>

            <div class="flex flex-wrap gap-2">
              <button
                class="px-3 py-1.5 rounded text-sm bg-surface-1 hover:bg-surface-2 text-text transition-colors flex items-center gap-2"
                onclick={() => togglePlugin(plugin)}
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
                  onclick={() => loadSettings(plugin.name)}
                  disabled={busyPlugin === plugin.name}
                >
                  <Settings2 size={14} />
                  Settings
                </button>
              {/if}

              {#if plugin.can_uninstall}
                <button
                  class="px-3 py-1.5 rounded text-sm bg-red-500/20 hover:bg-red-500/30 text-red-200 transition-colors flex items-center gap-2"
                  onclick={() => uninstallPlugin(plugin)}
                  disabled={busyPlugin === plugin.name}
                >
                  <Trash2 size={14} />
                  Uninstall
                </button>
              {/if}
            </div>
          </article>
        {/each}
      {/if}
    </div>
  {/if}

  {#if activeTab === "available"}
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
        onclick={() => loadRepository(true)}
        disabled={loadingAvailable}
      >
        <RefreshCw size={14} class={loadingAvailable ? "animate-spin" : ""} />
        Refresh
      </button>
    </div>

    <div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
      {#if loadingAvailable}
        <div class="col-span-full p-8 text-center text-overlay-1">Loading repository…</div>
      {:else if filteredRepository().length === 0}
        <div class="col-span-full p-8 rounded-xl bg-surface-0 text-overlay-1 text-center">
          No matching repository plugins.
        </div>
      {:else}
        {#each filteredRepository() as plugin}
          <article class="p-4 rounded-xl bg-surface-0 border border-border space-y-3">
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <h3 class="text-text font-semibold truncate">{plugin.name}</h3>
                <p class="text-xs text-overlay-1">{plugin.version} · {plugin.author || "Unknown author"}</p>
              </div>
              <div class="flex gap-1 shrink-0">
                {#if plugin.is_core}
                  <span class="px-2 py-1 rounded text-xs bg-blue-500/20 text-blue-300">core</span>
                {/if}
                {#if !plugin.compatible}
                  <span class="px-2 py-1 rounded text-xs bg-red-500/20 text-red-300">incompatible</span>
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
                onclick={() => installFromRepository(plugin.name)}
                disabled={busyPlugin === plugin.name || (!plugin.can_install && !plugin.can_update)}
              >
                <Download size={14} />
                {plugin.can_update ? "Update" : plugin.can_install ? "Install" : !plugin.compatible ? "Incompatible" : "Installed"}
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
  {/if}

  {#if activeTab === "settings"}
    {#if !selectedPlugin}
      <div class="p-8 rounded-xl bg-surface-0 text-overlay-1 text-center">
        Select a plugin with settings from the Installed tab.
      </div>
    {:else}
      <div class="p-4 rounded-xl bg-surface-0 border border-border space-y-4">
        <div class="flex items-center justify-between gap-3">
          <div>
            <h3 class="text-text font-semibold">{selectedPlugin}</h3>
            <p class="text-xs text-overlay-1">
              {selectedPluginInfo()?.description || "Plugin settings"}
            </p>
          </div>
          <div class="flex gap-2">
            <button
              class="px-3 py-2 rounded-lg bg-surface-1 hover:bg-surface-2 text-text transition-colors"
              onclick={resetDefaults}
              disabled={savingSettings}
            >
              Reset Defaults
            </button>
            <button
              class="px-3 py-2 rounded-lg bg-accent text-mantle font-semibold hover:bg-accent-hover transition-colors disabled:opacity-60"
              onclick={saveSettings}
              disabled={savingSettings}
            >
              {savingSettings ? "Saving..." : "Save"}
            </button>
          </div>
        </div>

        {#if definitions.length === 0}
          <div class="p-6 rounded-lg bg-surface-1 text-overlay-1 text-sm">
            This plugin has no configurable settings.
          </div>
        {:else}
          <div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
            {#each definitions as definition}
              <label class="p-3 rounded-lg bg-surface-1/70 border border-border/50 space-y-2">
                <div class="flex items-center justify-between gap-2">
                  <span class="text-sm font-medium text-text">{definition.label}</span>
                  {#if definition.restart_required}
                    <span class="text-[11px] px-1.5 py-0.5 rounded bg-yellow-500/20 text-yellow-200">restart</span>
                  {/if}
                </div>

                {#if definition.type === "bool"}
                  <input
                    type="checkbox"
                    checked={Boolean(values[definition.key])}
                    onchange={(event) =>
                      (values = {
                        ...values,
                        [definition.key]: (event.currentTarget as HTMLInputElement).checked,
                      })}
                  />
                {:else if definition.type === "select"}
                  <select
                    class="w-full px-3 py-2 rounded-md bg-surface-0 border border-border text-sm text-text"
                    value={String(values[definition.key] ?? definition.default ?? "")}
                    onchange={(event) =>
                      (values = {
                        ...values,
                        [definition.key]: parseInputValue(
                          definition,
                          (event.currentTarget as HTMLSelectElement).value,
                        ),
                      })}
                  >
                    {#each definition.options ?? [] as option}
                      <option value={option}>{option}</option>
                    {/each}
                  </select>
                {:else}
                  <input
                    class="w-full px-3 py-2 rounded-md bg-surface-0 border border-border text-sm text-text placeholder:text-overlay-1"
                    type={definition.secret ? "password" : definition.type === "int" || definition.type === "float" ? "number" : "text"}
                    min={definition.min}
                    max={definition.max}
                    step={definition.type === "float" ? "0.1" : definition.type === "int" ? "1" : undefined}
                    value={String(values[definition.key] ?? definition.default ?? "")}
                    oninput={(event) =>
                      (values = {
                        ...values,
                        [definition.key]: parseInputValue(
                          definition,
                          (event.currentTarget as HTMLInputElement).value,
                        ),
                      })}
                  />
                {/if}

                {#if definition.description}
                  <p class="text-xs text-overlay-1">{definition.description}</p>
                {/if}
              </label>
            {/each}
          </div>
        {/if}
      </div>
    {/if}
  {/if}
</div>
