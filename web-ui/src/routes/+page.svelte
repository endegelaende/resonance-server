<script lang="ts">
  import { playerStore } from "$lib/stores/player.svelte";
  import { colorStore } from "$lib/stores/color.svelte";
  import { uiStore } from "$lib/stores/ui.svelte";
  import { toastStore } from "$lib/stores/toast.svelte";
  import { api, type Track, type Artist, type Album } from "$lib/api";
  import NowPlaying from "$lib/components/NowPlaying.svelte";
  import PlayerSelector from "$lib/components/PlayerSelector.svelte";
  import TrackList from "$lib/components/TrackList.svelte";
  import SearchBar from "$lib/components/SearchBar.svelte";
  import Queue from "$lib/components/Queue.svelte";
  import Sidebar from "$lib/components/Sidebar.svelte";
  import AddFolderModal from "$lib/components/AddFolderModal.svelte";
  import SettingsPanel from "$lib/components/SettingsPanel.svelte";
  import ResizeHandle from "$lib/components/ResizeHandle.svelte";
  import {
    Library,
    Users,
    Disc3,
    ChevronRight,
    ArrowLeft,
    RefreshCw,
    FolderPlus,
    Menu,
    Wifi,
    WifiOff,
    Trash2,
    Loader2,
  } from "lucide-svelte";
  import { onMount } from "svelte";

  // ---------------------------------------------------------------------------
  // Panel sizes (resizable)
  // ---------------------------------------------------------------------------
  let sidebarWidth = $state(256);
  let queueWidth = $state(320);
  let sidebarCollapsed = $state(false);
  let queueCollapsed = $state(false);

  function handleSidebarResize(size: number) {
    sidebarWidth = size;
  }

  function handleQueueResize(size: number) {
    queueWidth = size;
  }

  function handleSidebarCollapse() {
    sidebarCollapsed = !sidebarCollapsed;
    if (sidebarCollapsed) {
      sidebarWidth = 0;
    } else {
      const saved = localStorage.getItem("resonance-sidebar-width");
      sidebarWidth = saved ? parseInt(saved, 10) : 256;
    }
  }

  function handleQueueCollapse() {
    queueCollapsed = !queueCollapsed;
    if (queueCollapsed) {
      queueWidth = 0;
    } else {
      const saved = localStorage.getItem("resonance-queue-width");
      queueWidth = saved ? parseInt(saved, 10) : 320;
    }
  }

  // ---------------------------------------------------------------------------
  // Bottom now-playing panel size (desktop)
  // ---------------------------------------------------------------------------
  let nowPlayingHeight = $state(280);
  const NOW_PLAYING_MIN_HEIGHT = 170;
  const NOW_PLAYING_MAX_HEIGHT = 520;

  let isNowPlayingDragging = false;
  let nowPlayingDragStartY = 0;
  let nowPlayingDragStartHeight = 0;

  function clampNowPlayingHeight(size: number): number {
    return Math.max(
      NOW_PLAYING_MIN_HEIGHT,
      Math.min(NOW_PLAYING_MAX_HEIGHT, Math.round(size)),
    );
  }

  function saveNowPlayingHeight(size: number): void {
    localStorage.setItem("resonance-nowplaying-height", size.toString());
  }

  function handleNowPlayingResizeMove(event: MouseEvent): void {
    if (!isNowPlayingDragging) return;
    const delta = nowPlayingDragStartY - event.clientY;
    nowPlayingHeight = clampNowPlayingHeight(nowPlayingDragStartHeight + delta);
  }

  function handleNowPlayingResizeEnd(): void {
    if (!isNowPlayingDragging) return;
    isNowPlayingDragging = false;
    saveNowPlayingHeight(nowPlayingHeight);
    document.removeEventListener("mousemove", handleNowPlayingResizeMove);
    document.removeEventListener("mouseup", handleNowPlayingResizeEnd);
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  }

  function handleNowPlayingResizeStart(event: MouseEvent): void {
    isNowPlayingDragging = true;
    nowPlayingDragStartY = event.clientY;
    nowPlayingDragStartHeight = nowPlayingHeight;
    event.preventDefault();
    document.addEventListener("mousemove", handleNowPlayingResizeMove);
    document.addEventListener("mouseup", handleNowPlayingResizeEnd);
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";
  }

  onMount(() => {
    const saved = localStorage.getItem("resonance-nowplaying-height");
    if (saved) {
      const parsed = parseInt(saved, 10);
      if (!Number.isNaN(parsed)) {
        nowPlayingHeight = clampNowPlayingHeight(parsed);
      }
    }
    return () => {
      document.removeEventListener("mousemove", handleNowPlayingResizeMove);
      document.removeEventListener("mouseup", handleNowPlayingResizeEnd);
    };
  });

  // ---------------------------------------------------------------------------
  // Pagination constants
  // ---------------------------------------------------------------------------
  const PAGE_SIZE = 50;

  // ---------------------------------------------------------------------------
  // Data + pagination state
  // ---------------------------------------------------------------------------
  let artists = $state<Artist[]>([]);
  let artistsTotal = $state(0);
  let artistsLoading = $state(false);

  let albums = $state<Album[]>([]);
  let albumsTotal = $state(0);
  let albumsLoading = $state(false);

  let tracks = $state<Track[]>([]);
  let tracksTotal = $state(0);
  let tracksLoading = $state(false);

  // Search results – now includes artists & albums too
  let searchArtists = $state<Artist[]>([]);
  let searchAlbums = $state<Album[]>([]);
  let searchTracks = $state<Track[]>([]);

  let isLoadingLibrary = $state(false);

  // Derived helpers for "has more" checks
  let hasMoreArtists = $derived(artists.length < artistsTotal);
  let hasMoreAlbums = $derived(albums.length < albumsTotal);
  let hasMoreTracks = $derived(tracks.length < tracksTotal);

  // Delete confirmation state
  let albumToDelete = $state<Album | null>(null);
  let isDeleting = $state(false);

  // Modal handling via store proxy
  let showAddFolderModal = $derived(uiStore.activeModal === "add-folder");
  let showDeleteConfirm = $derived(albumToDelete !== null);

  // ---------------------------------------------------------------------------
  // Infinite scroll sentinel refs
  // ---------------------------------------------------------------------------
  let artistsSentinel = $state<HTMLDivElement | null>(null);
  let albumsSentinel = $state<HTMLDivElement | null>(null);
  let tracksSentinel = $state<HTMLDivElement | null>(null);
  let scrollContainer = $state<HTMLDivElement | null>(null);

  // ---------------------------------------------------------------------------
  // IntersectionObserver for infinite scroll
  // ---------------------------------------------------------------------------
  let observer: IntersectionObserver | null = null;

  function setupObserver() {
    if (observer) observer.disconnect();

    observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const target = entry.target as HTMLElement;
          const kind = target.dataset.sentinel;
          if (kind === "artists" && hasMoreArtists && !artistsLoading) {
            loadMoreArtists();
          } else if (kind === "albums" && hasMoreAlbums && !albumsLoading) {
            loadMoreAlbums();
          } else if (kind === "tracks" && hasMoreTracks && !tracksLoading) {
            loadMoreTracks();
          }
        }
      },
      { root: null, rootMargin: "200px", threshold: 0 },
    );
  }

  function observeSentinels() {
    if (!observer) return;
    observer.disconnect();
    if (artistsSentinel) observer.observe(artistsSentinel);
    if (albumsSentinel) observer.observe(albumsSentinel);
    if (tracksSentinel) observer.observe(tracksSentinel);
  }

  // Re-observe whenever sentinel refs change
  $effect(() => {
    // Touch the refs so Svelte tracks them
    artistsSentinel;
    albumsSentinel;
    tracksSentinel;
    observeSentinels();
  });

  onMount(() => {
    setupObserver();
    return () => {
      if (observer) observer.disconnect();
    };
  });

  // ---------------------------------------------------------------------------
  // Breadcrumb navigation
  // ---------------------------------------------------------------------------
  const breadcrumbs = $derived(() => {
    const crumbs: Array<{ label: string; action: () => void }> = [
      { label: "Library", action: () => uiStore.navigateTo("artists") },
    ];

    if (uiStore.selectedArtist) {
      crumbs.push({
        label: uiStore.selectedArtist.name,
        action: () => {
          uiStore.selectedAlbum = null;
          uiStore.currentView = "albums";
        },
      });
    }

    if (uiStore.selectedAlbum) {
      crumbs.push({
        label: uiStore.selectedAlbum.name,
        action: () => {},
      });
    }

    return crumbs;
  });

  // ---------------------------------------------------------------------------
  // Data loading – initial (reset)
  // ---------------------------------------------------------------------------
  async function loadArtists() {
    isLoadingLibrary = true;
    artistsLoading = true;
    try {
      const result = await api.getArtists(0, PAGE_SIZE);
      artists = result.artists;
      artistsTotal = result.total;
    } catch (err) {
      console.error("Failed to load artists:", err);
      toastStore.error("Failed to load artists", {
        detail: (err as Error).message,
      });
    } finally {
      isLoadingLibrary = false;
      artistsLoading = false;
    }
  }

  async function loadAlbums(artistId?: string) {
    isLoadingLibrary = true;
    albumsLoading = true;
    try {
      const result = await api.getAlbums(0, PAGE_SIZE, artistId);
      albums = result.albums;
      albumsTotal = result.total;
    } catch (err) {
      console.error("Failed to load albums:", err);
      toastStore.error("Failed to load albums", {
        detail: (err as Error).message,
      });
    } finally {
      isLoadingLibrary = false;
      albumsLoading = false;
    }
  }

  async function loadTracks() {
    if (!uiStore.selectedAlbum) return;
    isLoadingLibrary = true;
    tracksLoading = true;
    try {
      const result = await api.getTracks(0, PAGE_SIZE, uiStore.selectedAlbum.id);
      tracks = result.tracks;
      tracksTotal = result.total;
    } catch (err) {
      console.error("Failed to load tracks:", err);
      toastStore.error("Failed to load tracks", {
        detail: (err as Error).message,
      });
    } finally {
      isLoadingLibrary = false;
      tracksLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Data loading – load MORE (append)
  // ---------------------------------------------------------------------------
  async function loadMoreArtists() {
    if (artistsLoading || !hasMoreArtists) return;
    artistsLoading = true;
    try {
      const result = await api.getArtists(artists.length, PAGE_SIZE);
      artists = [...artists, ...result.artists];
      artistsTotal = result.total;
    } catch (err) {
      console.error("Failed to load more artists:", err);
      toastStore.error("Failed to load more artists");
    } finally {
      artistsLoading = false;
    }
  }

  async function loadMoreAlbums() {
    if (albumsLoading || !hasMoreAlbums) return;
    albumsLoading = true;
    try {
      const result = await api.getAlbums(
        albums.length,
        PAGE_SIZE,
        uiStore.selectedArtist?.id,
      );
      albums = [...albums, ...result.albums];
      albumsTotal = result.total;
    } catch (err) {
      console.error("Failed to load more albums:", err);
      toastStore.error("Failed to load more albums");
    } finally {
      albumsLoading = false;
    }
  }

  async function loadMoreTracks() {
    if (tracksLoading || !hasMoreTracks) return;
    tracksLoading = true;
    try {
      const result = await api.getTracks(
        tracks.length,
        PAGE_SIZE,
        uiStore.selectedAlbum?.id,
      );
      tracks = [...tracks, ...result.tracks];
      tracksTotal = result.total;
    } catch (err) {
      console.error("Failed to load more tracks:", err);
      toastStore.error("Failed to load more tracks");
    } finally {
      tracksLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Search – now stores artists, albums AND tracks
  // ---------------------------------------------------------------------------
  async function handleSearch(query: string) {
    uiStore.navigateTo("search");
    isLoadingLibrary = true;
    try {
      const result = await api.search(query);
      searchArtists = result.artists;
      searchAlbums = result.albums;
      searchTracks = result.tracks;
    } catch (err) {
      console.error("Failed to search:", err);
      toastStore.error("Search failed", { detail: (err as Error).message });
    } finally {
      isLoadingLibrary = false;
    }
  }

  function handleClearSearch() {
    searchArtists = [];
    searchAlbums = [];
    searchTracks = [];
    if (uiStore.currentView === "search") {
      uiStore.navigateTo("artists");
    }
  }

  let hasSearchResults = $derived(
    searchArtists.length > 0 ||
      searchAlbums.length > 0 ||
      searchTracks.length > 0,
  );

  // ---------------------------------------------------------------------------
  // Rescan
  // ---------------------------------------------------------------------------
  async function handleRescan() {
    try {
      await api.rescan();
      toastStore.info("Library rescan started");
      if (uiStore.currentView === "artists") {
        loadArtists();
      }
    } catch (err) {
      console.error("Failed to start rescan:", err);
      toastStore.error("Failed to start rescan", {
        detail: (err as Error).message,
      });
    }
  }

  // ---------------------------------------------------------------------------
  // Add folder modal
  // ---------------------------------------------------------------------------
  function handleOpenAddFolder() {
    uiStore.openModal("add-folder");
  }

  function handleCloseAddFolder() {
    uiStore.closeModal();
    if (uiStore.currentView === "artists") {
      loadArtists();
    }
  }

  // ---------------------------------------------------------------------------
  // Album deletion handlers
  // ---------------------------------------------------------------------------
  function handleDeleteAlbumClick(album: Album, event: MouseEvent) {
    event.stopPropagation();
    albumToDelete = album;
  }

  function handleCancelDelete() {
    albumToDelete = null;
  }

  async function handleConfirmDelete() {
    if (!albumToDelete) return;
    const name = albumToDelete.name;

    isDeleting = true;
    try {
      await api.deleteAlbum(albumToDelete.id);
      toastStore.success(`Album "${name}" deleted`);

      await loadAlbums(uiStore.selectedArtist?.id);

      if (albums.length === 0) {
        uiStore.navigateTo("artists");
        await loadArtists();
      }
    } catch (err) {
      console.error("Failed to delete album:", err);
      toastStore.error(`Failed to delete album "${name}"`, {
        detail: (err as Error).message,
      });
    } finally {
      isDeleting = false;
      albumToDelete = null;
    }
  }

  // ---------------------------------------------------------------------------
  // Reactive Data Loading
  // ---------------------------------------------------------------------------
  $effect(() => {
    const view = uiStore.currentView;
    if (view === "artists") {
      loadArtists();
    } else if (view === "albums") {
      loadAlbums(uiStore.selectedArtist?.id);
    } else if (view === "tracks" && uiStore.selectedAlbum) {
      loadTracks();
    }
  });

  // Initial load & setup
  $effect(() => {
    colorStore.initialize();
  });
</script>

<div class="flex h-screen overflow-hidden">
  <!-- Left Sidebar Navigation -->
  {#if !sidebarCollapsed}
    <div
      style="width: {sidebarWidth}px; min-width: {sidebarWidth}px;"
      class="hidden lg:block shrink-0"
    >
      <Sidebar />
    </div>
  {/if}

  <!-- Sidebar Resize Handle (Desktop only) -->
  <div class="hidden lg:flex h-full">
    <ResizeHandle
      position="left"
      minSize={180}
      maxSize={400}
      defaultSize={256}
      storageKey="resonance-sidebar-width"
      onResize={handleSidebarResize}
      onCollapse={handleSidebarCollapse}
    />
  </div>

  <!-- Mobile Sidebar (unchanged behavior) -->
  <div class="lg:hidden">
    <Sidebar />
  </div>

  <!-- Main Content Area -->
  <div class="flex-1 flex flex-col min-w-0 bg-base">
    <!-- Header -->
    <header class="glass border-b border-border px-6 py-4">
      <div class="flex items-center justify-between gap-4">
        <!-- Sidebar Toggle (Mobile/Tablet) & Status -->
        <div class="flex items-center gap-3">
          <button
            class="lg:hidden p-2 -ml-2 rounded-lg hover:bg-surface-0 text-overlay-1 hover:text-text transition-colors"
            onclick={() => uiStore.toggleSidebar()}
          >
            <Menu size={24} />
          </button>

          <div class="flex items-center gap-2 text-xs text-overlay-1">
            {#if playerStore.isConnected}
              <Wifi size={14} class="text-success" />
              <span class="hidden sm:inline">Connected</span>
            {:else}
              <WifiOff size={14} class="text-error" />
              <span class="hidden sm:inline">Disconnected</span>
            {/if}
          </div>
        </div>

        <!-- Search -->
        <div class="flex-1 max-w-xl">
          <SearchBar onSearch={handleSearch} onClear={handleClearSearch} />
        </div>

        <!-- Player Selector -->
        <div class="w-48 sm:w-64">
          <PlayerSelector />
        </div>
      </div>
    </header>

    <!-- Content -->
    <div class="flex-1 flex overflow-hidden">
      <!-- Library Browser -->
      <main class="flex-1 flex flex-col min-w-0 overflow-hidden relative">
        <!-- Library Header (Breadcrumbs & Actions) -->
        {#if uiStore.currentView !== "settings" && uiStore.currentView !== "playlists"}
          <div
            class="flex items-center justify-between px-6 py-4 border-b border-border bg-base/50 backdrop-blur-sm z-10"
          >
            <div class="flex items-center gap-4 overflow-hidden">
              {#if uiStore.selectedArtist || uiStore.selectedAlbum}
                <button
                  class="p-2 rounded-lg hover:bg-surface-0 text-overlay-1 hover:text-text transition-colors shrink-0"
                  onclick={() => uiStore.goBack()}
                  aria-label="Go back"
                >
                  <ArrowLeft size={20} />
                </button>
              {/if}

              <!-- Breadcrumbs -->
              <nav
                class="flex items-center gap-1 text-sm overflow-hidden whitespace-nowrap mask-linear-fade"
              >
                {#each breadcrumbs() as crumb, index}
                  {#if index > 0}
                    <ChevronRight size={16} class="text-overlay-0 shrink-0" />
                  {/if}
                  <button
                    class="px-2 py-1 rounded hover:bg-surface-0 transition-colors truncate max-w-[200px]
                      {index === breadcrumbs().length - 1
                      ? 'text-text font-medium'
                      : 'text-overlay-1'}"
                    onclick={crumb.action}
                  >
                    {crumb.label}
                  </button>
                {/each}
              </nav>
            </div>

            <div class="flex items-center gap-2 shrink-0">
              <button
                class="flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-surface-0 text-overlay-1 hover:text-text transition-colors"
                onclick={handleOpenAddFolder}
                aria-label="Add music folder"
              >
                <FolderPlus size={18} />
                <span class="text-sm hidden sm:inline">Add Folder</span>
              </button>
              <button
                class="flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-surface-0 text-overlay-1 hover:text-text transition-colors"
                onclick={handleRescan}
                aria-label="Rescan library"
              >
                <RefreshCw
                  size={18}
                  class={isLoadingLibrary ? "animate-spin" : ""}
                />
                <span class="text-sm hidden sm:inline">Rescan</span>
              </button>
            </div>
          </div>
        {/if}

        <!-- Library Content -->
        <div class="flex-1 overflow-y-auto" bind:this={scrollContainer}>
          {#if uiStore.currentView === "artists"}
            <!-- Artists Grid -->
            <div
              class="p-6 grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-7 gap-4"
            >
              {#each artists as artist}
                <button
                  class="group flex flex-col items-center gap-3 p-4 rounded-xl hover:bg-surface-0 transition-colors"
                  onclick={() => uiStore.viewArtist(artist)}
                >
                  <div
                    class="w-24 h-24 sm:w-32 sm:h-32 rounded-full bg-surface-1 flex items-center justify-center group-hover:bg-surface-2 transition-colors relative overflow-hidden"
                  >
                    <Users
                      size={48}
                      class="text-overlay-0 group-hover:text-accent transition-colors relative z-10"
                    />
                    <div
                      class="absolute inset-0 bg-gradient-to-tr from-surface-1 to-surface-0 opacity-0 group-hover:opacity-100 transition-opacity"
                    ></div>
                  </div>
                  <div class="text-center min-w-0 w-full">
                    <p class="text-text font-medium truncate">{artist.name}</p>
                    <p class="text-sm text-overlay-1">
                      {artist.albumCount} albums
                    </p>
                  </div>
                </button>
              {/each}
            </div>

            <!-- Infinite scroll sentinel + counter -->
            {#if hasMoreArtists}
              <div
                bind:this={artistsSentinel}
                data-sentinel="artists"
                class="flex items-center justify-center py-8"
              >
                {#if artistsLoading}
                  <Loader2 size={24} class="animate-spin dynamic-accent color-transition" />
                {:else}
                  <button
                    class="px-4 py-2 text-sm rounded-lg bg-surface-0 hover:bg-surface-1 text-overlay-1 hover:text-text transition-colors"
                    onclick={loadMoreArtists}
                  >
                    Load more ({artists.length} / {artistsTotal})
                  </button>
                {/if}
              </div>
            {:else if artists.length > 0}
              <div class="text-center text-xs text-overlay-0 py-4">
                {artists.length} artists
              </div>
            {/if}

            {#if artists.length === 0 && !isLoadingLibrary}
              <div
                class="flex flex-col items-center justify-center h-full text-overlay-1 p-8"
              >
                <div
                  class="w-20 h-20 rounded-full bg-surface-0 flex items-center justify-center mb-6"
                >
                  <Library size={40} class="opacity-50" />
                </div>
                <h3 class="text-xl font-medium text-text mb-2">
                  Your library is empty
                </h3>
                <p class="text-sm mb-6 text-center max-w-sm">
                  Add a music folder to start scanning your collection. Supports
                  local folders with FLAC, MP3, and more.
                </p>
                <button
                  class="px-4 py-2 bg-accent text-mantle font-medium rounded-lg hover:bg-accent-hover transition-colors"
                  onclick={handleOpenAddFolder}
                >
                  Add Music Folder
                </button>
              </div>
            {/if}
          {:else if uiStore.currentView === "albums"}
            <!-- Albums Grid -->
            <div
              class="p-6 grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-6"
            >
              {#each albums as album}
                <div class="group relative">
                  <button
                    class="flex flex-col gap-3 p-3 -m-3 rounded-xl hover:bg-surface-0 transition-colors text-left w-full"
                    onclick={() => uiStore.viewAlbum(album)}
                  >
                    <div
                      class="aspect-square rounded-lg bg-surface-1 flex items-center justify-center group-hover:bg-surface-2 transition-colors overflow-hidden shadow-lg group-hover:shadow-xl group-hover:scale-102 duration-300 relative"
                    >
                      {#if album.coverArt}
                        <img
                          src={album.coverArt}
                          alt={album.name}
                          class="w-full h-full object-cover"
                        />
                        <div
                          class="absolute inset-0 bg-black/0 group-hover:bg-black/10 transition-colors"
                        ></div>
                      {:else}
                        <Disc3
                          size={48}
                          class="text-overlay-0 group-hover:text-accent transition-colors"
                        />
                      {/if}
                    </div>
                    <div class="min-w-0 px-1">
                      <p class="text-text font-medium truncate">{album.name}</p>
                      <p class="text-sm text-overlay-1 truncate">
                        {album.artist}
                      </p>
                      {#if album.year}
                        <p class="text-xs text-overlay-0 mt-0.5">
                          {album.year}
                        </p>
                      {/if}
                    </div>
                  </button>
                  <!-- Delete button (shown on hover) -->
                  <button
                    class="absolute top-1 right-1 p-1.5 rounded-full bg-error/80 text-white opacity-0 group-hover:opacity-100 hover:bg-error transition-all shadow-lg"
                    onclick={(e) => handleDeleteAlbumClick(album, e)}
                    aria-label="Delete album"
                    title="Delete album from library"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              {/each}
            </div>

            <!-- Infinite scroll sentinel + counter -->
            {#if hasMoreAlbums}
              <div
                bind:this={albumsSentinel}
                data-sentinel="albums"
                class="flex items-center justify-center py-8"
              >
                {#if albumsLoading}
                  <Loader2 size={24} class="animate-spin dynamic-accent color-transition" />
                {:else}
                  <button
                    class="px-4 py-2 text-sm rounded-lg bg-surface-0 hover:bg-surface-1 text-overlay-1 hover:text-text transition-colors"
                    onclick={loadMoreAlbums}
                  >
                    Load more ({albums.length} / {albumsTotal})
                  </button>
                {/if}
              </div>
            {:else if albums.length > 0}
              <div class="text-center text-xs text-overlay-0 py-4">
                {albums.length} albums
              </div>
            {/if}
          {:else if uiStore.currentView === "tracks"}
            <!-- Track List -->
            <TrackList
              {tracks}
              showAlbum={false}
              highlightId={playerStore.currentTrack?.id}
              albumId={uiStore.selectedAlbum?.id}
            />

            <!-- Infinite scroll sentinel for tracks -->
            {#if hasMoreTracks}
              <div
                bind:this={tracksSentinel}
                data-sentinel="tracks"
                class="flex items-center justify-center py-8"
              >
                {#if tracksLoading}
                  <Loader2 size={24} class="animate-spin dynamic-accent color-transition" />
                {:else}
                  <button
                    class="px-4 py-2 text-sm rounded-lg bg-surface-0 hover:bg-surface-1 text-overlay-1 hover:text-text transition-colors"
                    onclick={loadMoreTracks}
                  >
                    Load more ({tracks.length} / {tracksTotal})
                  </button>
                {/if}
              </div>
            {:else if tracks.length > 0}
              <div class="text-center text-xs text-overlay-0 py-4">
                {tracks.length} tracks
              </div>
            {/if}
          {:else if uiStore.currentView === "search"}
            <!-- ============================================================ -->
            <!-- Search Results – Artists, Albums, Tracks                      -->
            <!-- ============================================================ -->
            <div class="p-6 space-y-8">
              {#if hasSearchResults}
                <!-- Search: Artists -->
                {#if searchArtists.length > 0}
                  <section>
                    <h3 class="text-sm font-semibold text-overlay-1 uppercase tracking-wider mb-4">
                      Artists
                      <span class="text-overlay-0 font-normal normal-case tracking-normal ml-1">
                        ({searchArtists.length})
                      </span>
                    </h3>
                    <div class="flex gap-4 overflow-x-auto pb-2">
                      {#each searchArtists as artist}
                        <button
                          class="group flex flex-col items-center gap-2 p-3 rounded-xl hover:bg-surface-0 transition-colors shrink-0 w-28"
                          onclick={() => uiStore.viewArtist(artist)}
                        >
                          <div
                            class="w-20 h-20 rounded-full bg-surface-1 flex items-center justify-center group-hover:bg-surface-2 transition-colors"
                          >
                            <Users
                              size={32}
                              class="text-overlay-0 group-hover:text-accent transition-colors"
                            />
                          </div>
                          <p class="text-sm text-text font-medium truncate w-full text-center">
                            {artist.name}
                          </p>
                        </button>
                      {/each}
                    </div>
                  </section>
                {/if}

                <!-- Search: Albums -->
                {#if searchAlbums.length > 0}
                  <section>
                    <h3 class="text-sm font-semibold text-overlay-1 uppercase tracking-wider mb-4">
                      Albums
                      <span class="text-overlay-0 font-normal normal-case tracking-normal ml-1">
                        ({searchAlbums.length})
                      </span>
                    </h3>
                    <div class="flex gap-4 overflow-x-auto pb-2">
                      {#each searchAlbums as album}
                        <button
                          class="group flex flex-col gap-2 p-2 rounded-xl hover:bg-surface-0 transition-colors shrink-0 w-36 text-left"
                          onclick={() => uiStore.viewAlbum(album)}
                        >
                          <div
                            class="aspect-square rounded-lg bg-surface-1 flex items-center justify-center group-hover:bg-surface-2 transition-colors overflow-hidden shadow-md w-full"
                          >
                            {#if album.coverArt}
                              <img
                                src={album.coverArt}
                                alt={album.name}
                                class="w-full h-full object-cover"
                              />
                            {:else}
                              <Disc3
                                size={32}
                                class="text-overlay-0 group-hover:text-accent transition-colors"
                              />
                            {/if}
                          </div>
                          <div class="min-w-0 w-full">
                            <p class="text-sm text-text font-medium truncate">
                              {album.name}
                            </p>
                            <p class="text-xs text-overlay-1 truncate">
                              {album.artist}
                            </p>
                          </div>
                        </button>
                      {/each}
                    </div>
                  </section>
                {/if}

                <!-- Search: Tracks -->
                {#if searchTracks.length > 0}
                  <section>
                    <h3 class="text-sm font-semibold text-overlay-1 uppercase tracking-wider mb-4">
                      Tracks
                      <span class="text-overlay-0 font-normal normal-case tracking-normal ml-1">
                        ({searchTracks.length})
                      </span>
                    </h3>
                    <TrackList
                      tracks={searchTracks}
                      highlightId={playerStore.currentTrack?.id}
                    />
                  </section>
                {/if}
              {:else if !isLoadingLibrary}
                <div class="text-overlay-1 text-center py-12">
                  <p>No results found</p>
                </div>
              {/if}
            </div>
          {:else if uiStore.currentView === "playlists"}
            <div
              class="flex flex-col items-center justify-center h-full text-overlay-1"
            >
              <div
                class="w-16 h-16 rounded-full bg-surface-0 flex items-center justify-center mb-4"
              >
                <Users size={32} class="opacity-50" />
              </div>
              <p>Playlists coming soon</p>
            </div>
          {:else if uiStore.currentView === "settings"}
            <SettingsPanel />
          {/if}

          {#if isLoadingLibrary}
            <div class="flex items-center justify-center py-12">
              <RefreshCw
                size={32}
                class="dynamic-accent color-transition animate-spin"
              />
            </div>
          {/if}
        </div>

        <!-- Now Playing Resize Handle (Desktop) -->
        <div
          class="hidden lg:flex h-3 shrink-0 items-center justify-center cursor-row-resize group"
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize now playing panel"
          onmousedown={handleNowPlayingResizeStart}
        >
          <div
            class="h-[2px] w-full rounded-full bg-overlay-0/60 group-hover:bg-accent transition-colors"
          ></div>
        </div>

        <!-- Now Playing Bar (Bottom) -->
        <div
          class="border-t border-border p-4 bg-mantle/50 backdrop-blur-md z-20 shrink-0 overflow-hidden lg:h-[var(--now-playing-height)] lg:min-h-[var(--now-playing-height)]"
          style="--now-playing-height: {nowPlayingHeight}px;"
        >
          <NowPlaying />
        </div>
      </main>

      <!-- Queue Resize Handle (Desktop only) -->
      {#if !queueCollapsed}
        <div class="hidden 2xl:flex h-full">
          <ResizeHandle
            position="right"
            minSize={250}
            maxSize={500}
            defaultSize={320}
            storageKey="resonance-queue-width"
            onResize={handleQueueResize}
            onCollapse={handleQueueCollapse}
          />
        </div>
      {/if}

      <!-- Queue Sidebar -->
      {#if !queueCollapsed}
        <aside
          class="border-l border-border bg-mantle hidden 2xl:flex flex-col shrink-0"
          style="width: {queueWidth}px; min-width: {queueWidth}px;"
        >
          <Queue />
        </aside>
      {/if}
    </div>
  </div>
</div>

<!-- Add Folder Modal -->
{#if showAddFolderModal}
  <AddFolderModal isOpen={true} onClose={handleCloseAddFolder} />
{/if}

<!-- Delete Album Confirmation Modal -->
{#if showDeleteConfirm && albumToDelete}
  <div class="fixed inset-0 z-50 flex items-center justify-center">
    <!-- Backdrop -->
    <button
      class="absolute inset-0 bg-black/60 backdrop-blur-sm"
      onclick={handleCancelDelete}
      aria-label="Cancel"
    ></button>

    <!-- Modal -->
    <div
      class="relative bg-mantle border border-border rounded-xl shadow-2xl p-6 max-w-md w-full mx-4"
    >
      <h2 class="text-xl font-semibold text-text mb-2">Delete Album?</h2>
      <p class="text-overlay-1 mb-4">
        Are you sure you want to delete <strong class="text-text"
          >"{albumToDelete.name}"</strong
        >
        by {albumToDelete.artist}?
      </p>
      <p class="text-sm text-overlay-0 mb-6">
        This will permanently remove all tracks from this album from your
        library. The files on disk will not be deleted.
      </p>

      <div class="flex gap-3 justify-end">
        <button
          class="px-4 py-2 rounded-lg hover:bg-surface-0 text-overlay-1 hover:text-text transition-colors"
          onclick={handleCancelDelete}
          disabled={isDeleting}
        >
          Cancel
        </button>
        <button
          class="px-4 py-2 rounded-lg bg-error text-white hover:bg-error/80 transition-colors flex items-center gap-2"
          onclick={handleConfirmDelete}
          disabled={isDeleting}
        >
          {#if isDeleting}
            <RefreshCw size={16} class="animate-spin" />
            Deleting...
          {:else}
            <Trash2 size={16} />
            Delete Album
          {/if}
        </button>
      </div>
    </div>
  </div>
{/if}
