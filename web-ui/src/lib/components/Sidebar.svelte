<script lang="ts">
  import { uiStore, type View } from "$lib/stores/ui.svelte";
  import { api, type PluginUIRegistryEntry } from "$lib/api";
  import DynamicIcon from "$lib/components/DynamicIcon.svelte";
  import {
    Library,
    Search,
    Settings,
    ListMusic,
    Disc3,
    Users,
    X,
    Puzzle,
  } from "lucide-svelte";
  import { onMount } from "svelte";

  // Dynamic plugin UI entries
  let pluginNavItems = $state<PluginUIRegistryEntry[]>([]);

  onMount(() => {
    loadPluginNav();
  });

  async function loadPluginNav() {
    try {
      pluginNavItems = await api.getPluginUIRegistry();
    } catch {
      pluginNavItems = [];
    }
  }

  function handleNavigate(view: View) {
    uiStore.navigateTo(view);
    if (typeof window !== "undefined" && window.innerWidth < 1024) {
      uiStore.setSidebarOpen(false);
    }
  }

  function handleNavigatePlugin(pluginId: string) {
    uiStore.navigateToPlugin(pluginId);
    if (typeof window !== "undefined" && window.innerWidth < 1024) {
      uiStore.setSidebarOpen(false);
    }
  }

  function isActive(view: string): boolean {
    return uiStore.currentView === view;
  }
</script>

<!-- Mobile Backdrop -->
{#if uiStore.isSidebarOpen}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="fixed inset-0 bg-black/50 z-40 lg:hidden transition-opacity duration-300"
    onclick={() => uiStore.setSidebarOpen(false)}
  ></div>
{/if}

<aside
  class="
	fixed lg:static inset-y-0 left-0 z-50
	w-64 lg:w-full bg-mantle flex flex-col h-full
	transition-transform duration-300 ease-in-out shadow-2xl lg:shadow-none
	{uiStore.isSidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}
"
>
  <!-- Logo -->
  <div class="h-[73px] px-5 flex items-center justify-between shrink-0">
    <div class="flex items-center gap-3">
      <div class="w-10 h-10 rounded-lg overflow-hidden flex items-center justify-center">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 200 200"
          class="w-10 h-10"
          aria-label="Resonance logo"
          role="img"
        >
          <defs>
            <linearGradient id="sidebarLogoWarm" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stop-color="#f0c27a" />
              <stop offset="100%" stop-color="#e09f5a" />
            </linearGradient>
          </defs>
          <circle cx="100" cy="100" r="92" fill="#1a1614" />
          <text
            x="80"
            y="130"
            text-anchor="middle"
            font-family="system-ui,sans-serif"
            font-size="100"
            font-weight="800"
            fill="url(#sidebarLogoWarm)"
          >
            R
          </text>
          <path
            d="M125,65 Q145,100 125,135"
            fill="none"
            stroke="url(#sidebarLogoWarm)"
            stroke-width="4"
            stroke-linecap="round"
            opacity="0.5"
          />
          <path
            d="M140,52 Q165,100 140,148"
            fill="none"
            stroke="url(#sidebarLogoWarm)"
            stroke-width="3"
            stroke-linecap="round"
            opacity="0.25"
          />
        </svg>
      </div>
      <span class="text-text font-semibold text-sm tracking-wide">Resonance</span>
    </div>

    <!-- Mobile Close -->
    <button
      class="lg:hidden p-2 -mr-2 text-overlay-1 hover:text-text rounded-lg hover:bg-surface-0 transition-colors"
      onclick={() => uiStore.setSidebarOpen(false)}
      aria-label="Close sidebar"
    >
      <X size={18} />
    </button>
  </div>

  <!-- Navigation -->
  <nav class="flex-1 px-3 py-3 overflow-y-auto space-y-0.5">

    <!-- Library & Search -->
    <button
      class="sidebar-item {isActive('artists') && !uiStore.selectedArtist ? 'active' : ''}"
      onclick={() => handleNavigate("artists")}
    >
      <Library size={18} />
      <span>Library</span>
    </button>

    <button
      class="sidebar-item {isActive('search') ? 'active' : ''}"
      onclick={() => handleNavigate("search")}
    >
      <Search size={18} />
      <span>Search</span>
    </button>

    <!-- Subtle divider -->
    <div class="h-px bg-border/40 mx-2 my-3"></div>

    <!-- Collections -->
    <button
      class="sidebar-item {isActive('artists') && !uiStore.selectedArtist ? '' : isActive('artists') || uiStore.selectedArtist ? 'active' : ''}"
      onclick={() => handleNavigate("artists")}
    >
      <Users size={18} />
      <span>Artists</span>
    </button>

    <button
      class="sidebar-item {isActive('albums') && !uiStore.selectedAlbum ? 'active' : ''}"
      onclick={() => handleNavigate("albums")}
    >
      <Disc3 size={18} />
      <span>Albums</span>
    </button>

    <button
      class="sidebar-item {isActive('playlists') ? 'active' : ''}"
      onclick={() => handleNavigate("playlists")}
    >
      <ListMusic size={18} />
      <span>Playlists</span>
    </button>

    <!-- Plugin Pages -->
    {#if pluginNavItems.length > 0}
      <div class="h-px bg-border/40 mx-2 my-3"></div>

      {#each pluginNavItems as item}
        <button
          class="sidebar-item {uiStore.currentView === `plugin:${item.id}` ? 'active' : ''}"
          onclick={() => handleNavigatePlugin(item.id)}
        >
          <DynamicIcon
            name={item.icon}
            size={18}
            class="transition-colors"
          />
          <span>{item.label}</span>
        </button>
      {/each}
    {/if}
  </nav>

  <!-- Footer -->
  <div class="px-3 py-3 shrink-0 space-y-0.5">
    <div class="h-px bg-border/40 mx-2 mb-2"></div>

    <button
      class="sidebar-item {isActive('plugins') ? 'active' : ''}"
      onclick={() => handleNavigate("plugins")}
    >
      <Puzzle size={18} />
      <span>Plugins</span>
    </button>

    <button
      class="sidebar-item {isActive('settings') ? 'active' : ''}"
      onclick={() => handleNavigate("settings")}
    >
      <Settings size={18} />
      <span>Settings</span>
    </button>
  </div>
</aside>

<style>
  .sidebar-item {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.5rem 0.75rem;
    border-radius: 0.5rem;
    font-size: 0.8125rem;
    font-weight: 450;
    color: var(--color-overlay-1);
    transition: all 150ms ease;
    text-align: left;
    cursor: pointer;
    border: none;
    background: none;
    letter-spacing: 0.01em;
  }

  .sidebar-item:hover {
    color: var(--color-text);
    background-color: var(--color-surface-0);
  }

  .sidebar-item.active {
    color: var(--color-text);
    background-color: var(--color-surface-0);
    font-weight: 500;
  }

  .sidebar-item.active :global(svg) {
    color: var(--dynamic-accent, var(--color-accent));
    transition: color var(--color-transition);
  }
</style>
