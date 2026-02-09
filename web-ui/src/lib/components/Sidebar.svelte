<script lang="ts">
  import { uiStore, type View } from "$lib/stores/ui.svelte";
  import {
    Library,
    Search,
    Settings,
    ListMusic,
    Disc3,
    Users,
    X,
  } from "lucide-svelte";

  // Navigation items configuration
  const mainNavItems = [
    {
      id: "artists",
      label: "Library",
      icon: Library,
      view: "artists" as View,
    },
    {
      id: "search",
      label: "Search",
      icon: Search,
      view: "search" as View,
    },
  ] as const;

  const secondaryNavItems = [
    {
      id: "settings",
      label: "Settings",
      icon: Settings,
      view: "settings" as View,
    },
  ] as const;

  function handleNavigate(view: View) {
    uiStore.navigateTo(view);
    // Auto-close on mobile
    if (typeof window !== "undefined" && window.innerWidth < 1024) {
      uiStore.setSidebarOpen(false);
    }
  }
</script>

<!-- Mobile Backdrop -->
{#if uiStore.isSidebarOpen}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 lg:hidden transition-opacity duration-300"
    onclick={() => uiStore.setSidebarOpen(false)}
  ></div>
{/if}

<aside
  class="
	fixed lg:static inset-y-0 left-0 z-50
	w-64 lg:w-full bg-mantle lg:border-r-0 border-r border-border flex flex-col h-full
	transition-transform duration-300 ease-in-out shadow-2xl lg:shadow-none
	{uiStore.isSidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}
"
>
  <!-- Logo Area -->
  <div
    class="h-[73px] px-6 flex items-center justify-between border-b border-border shrink-0"
  >
    <div class="flex items-center">
      <div
        class="w-14 h-14 rounded-lg overflow-hidden flex items-center justify-center"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 200 200"
          class="w-14 h-14"
          aria-label="Resonance logo"
          role="img"
        >
          <defs>
            <linearGradient id="sidebarLogoWarm" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stop-color="#f59e0b" />
              <stop offset="100%" stop-color="#ef4444" />
            </linearGradient>
          </defs>
          <circle cx="100" cy="100" r="92" fill="#1a120e" />
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
            opacity="0.6"
          />
          <path
            d="M140,52 Q165,100 140,148"
            fill="none"
            stroke="url(#sidebarLogoWarm)"
            stroke-width="3"
            stroke-linecap="round"
            opacity="0.35"
          />
        </svg>
      </div>
    </div>
    <!-- Mobile Close Button -->
    <button
      class="lg:hidden p-2 -mr-2 text-overlay-1 hover:text-text rounded-lg hover:bg-surface-0 transition-colors"
      onclick={() => uiStore.setSidebarOpen(false)}
      aria-label="Close sidebar"
    >
      <X size={20} />
    </button>
  </div>

  <!-- Main Navigation -->
  <nav class="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
    <div
      class="px-3 py-2 text-xs font-semibold text-overlay-0 uppercase tracking-wider mb-2"
    >
      Menu
    </div>

    {#each mainNavItems as item}
      <button
        class="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-200 group
				{uiStore.currentView === item.view
          ? 'bg-surface-0 text-accent dynamic-accent font-medium'
          : 'text-overlay-1 hover:text-text hover:bg-surface-0'}"
        onclick={() => handleNavigate(item.view)}
      >
        <item.icon
          size={20}
          class="transition-colors {uiStore.currentView === item.view
            ? 'text-accent dynamic-accent'
            : 'group-hover:text-text'}"
        />
        <span>{item.label}</span>
      </button>
    {/each}

    <!-- Divider -->
    <div class="my-4 border-t border-surface-1 mx-3 opacity-50"></div>

    <!-- Quick Filters / Collections -->
    <div
      class="px-3 py-2 text-xs font-semibold text-overlay-0 uppercase tracking-wider mb-2"
    >
      Collections
    </div>

    <button
      class="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-200 group
			{uiStore.currentView === 'artists' && !uiStore.selectedArtist
        ? 'bg-surface-0 text-accent dynamic-accent font-medium'
        : 'text-overlay-1 hover:text-text hover:bg-surface-0'}"
      onclick={() => handleNavigate("artists")}
    >
      <Users size={20} class="transition-colors group-hover:text-text" />
      <span>Artists</span>
    </button>

    <button
      class="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-200 group
			{uiStore.currentView === 'albums' && !uiStore.selectedAlbum
        ? 'bg-surface-0 text-accent dynamic-accent font-medium'
        : 'text-overlay-1 hover:text-text hover:bg-surface-0'}"
      onclick={() => handleNavigate("albums")}
    >
      <Disc3 size={20} class="transition-colors group-hover:text-text" />
      <span>Albums</span>
    </button>

    <button
      class="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-200 group
			{uiStore.currentView === 'playlists'
        ? 'bg-surface-0 text-accent dynamic-accent font-medium'
        : 'text-overlay-1 hover:text-text hover:bg-surface-0'}"
      onclick={() => handleNavigate("playlists")}
    >
      <ListMusic size={20} class="transition-colors group-hover:text-text" />
      <span>Playlists</span>
    </button>
  </nav>

  <!-- Footer / Settings -->
  <div class="p-3 border-t border-border mt-auto shrink-0">
    {#each secondaryNavItems as item}
      <button
        class="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-200 group
				{uiStore.currentView === item.view
          ? 'bg-surface-0 text-accent dynamic-accent font-medium'
          : 'text-overlay-1 hover:text-text hover:bg-surface-0'}"
        onclick={() => handleNavigate(item.view)}
      >
        <item.icon
          size={20}
          class="transition-colors {uiStore.currentView === item.view
            ? 'text-accent dynamic-accent'
            : 'group-hover:text-text'}"
        />
        <span>{item.label}</span>
      </button>
    {/each}
  </div>
</aside>
