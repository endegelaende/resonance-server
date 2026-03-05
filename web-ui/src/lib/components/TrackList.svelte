<script lang="ts">
  import { playerStore } from "$lib/stores/player.svelte";
  import type { Track } from "$lib/api";
  import { Play, Plus, Clock, Music2, ListPlus, Shuffle } from "lucide-svelte";

  interface Props {
    tracks: Track[];
    showAlbum?: boolean;
    showArtist?: boolean;
    highlightId?: number | null;
    albumName?: string;
    albumId?: string | null;
  }

  let {
    tracks,
    showAlbum = true,
    showArtist = true,
    highlightId = null,
    albumName = "",
    albumId = null,
  }: Props = $props();

  // Prevent duplicate actions (double-clicks / rapid clicks) from starting overlapping async flows.
  let isPlayInFlight = $state(false);
  let inFlightKey = $state<string | null>(null);

  // Format duration to mm:ss
  function formatDuration(seconds: number): string {
    if (!seconds || seconds < 0) return "--:--";
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  }

  // Calculate total album duration
  function getTotalDuration(): string {
    const total = tracks.reduce((acc, t) => acc + (t.duration || 0), 0);
    const hours = Math.floor(total / 3600);
    const mins = Math.floor((total % 3600) / 60);
    if (hours > 0) {
      return `${hours}h ${mins}m`;
    }
    return `${mins} min`;
  }

  async function handlePlayAlbum() {
    if (isPlayInFlight || tracks.length === 0) return;

    isPlayInFlight = true;
    inFlightKey = "play-album";

    try {
      console.log(
        "[TrackList] handlePlayAlbum: Playing entire album from start, albumId:",
        albumId,
      );

      // If we have an albumId, use the efficient loadtracks command
      // This does clear + add all + play in one atomic server operation
      if (albumId) {
        await playerStore.playAlbum(albumId);
        await playerStore.loadPlaylist();
        await playerStore.loadStatus();
      } else {
        // Fallback: manual clear + add + jump (less reliable)
        await playerStore.clearPlaylist();

        for (const t of tracks) {
          await playerStore.addToPlaylist(t);
        }

        await playerStore.jumpToIndex(0, tracks[0]);
        await playerStore.loadPlaylist();
      }

      console.log("[TrackList] handlePlayAlbum complete");
    } finally {
      isPlayInFlight = false;
      inFlightKey = null;
    }
  }

  async function handleShuffleAlbum() {
    if (isPlayInFlight || tracks.length === 0) return;

    isPlayInFlight = true;
    inFlightKey = "shuffle-album";

    try {
      console.log("[TrackList] handleShuffleAlbum: Shuffling album");

      // Shuffle the tracks array
      const shuffled = [...tracks].sort(() => Math.random() - 0.5);

      await playerStore.clearPlaylist();

      for (const t of shuffled) {
        await playerStore.addToPlaylist(t);
      }

      await playerStore.jumpToIndex(0, shuffled[0]);
      await playerStore.loadPlaylist();

      console.log("[TrackList] handleShuffleAlbum complete");
    } finally {
      isPlayInFlight = false;
      inFlightKey = null;
    }
  }

  async function handleAddAllToQueue() {
    if (isPlayInFlight || tracks.length === 0) return;

    isPlayInFlight = true;
    inFlightKey = "add-all";

    try {
      console.log(
        "[TrackList] handleAddAllToQueue: Adding all tracks to queue",
      );

      for (const t of tracks) {
        await playerStore.addToPlaylist(t);
      }

      await playerStore.loadPlaylist();

      console.log("[TrackList] handleAddAllToQueue complete");
    } finally {
      isPlayInFlight = false;
      inFlightKey = null;
    }
  }

  async function handlePlay(track: Track, index: number) {
    const key = `${track.id ?? "noid"}|${track.path ?? ""}|${index}`;

    // Hard guard: if an action is in-flight, ignore further clicks (including double-click).
    // This avoids overlapping clear/add/jump sequences that can corrupt UI state.
    if (isPlayInFlight) {
      console.log("[TrackList] handlePlay ignored (in-flight):", {
        track: track.title,
        index,
        key,
        inFlightKey,
      });
      return;
    }

    isPlayInFlight = true;
    inFlightKey = key;

    try {
      console.log("[TrackList] handlePlay called:", {
        track: track.title,
        index,
        path: track.path,
      });

      // If we have albumId and clicking first track, use efficient loadtracks
      if (albumId && index === 0) {
        console.log("[TrackList] Using playAlbum for first track");
        await playerStore.playAlbum(albumId);
        await playerStore.loadPlaylist();
        await playerStore.loadStatus();
      } else if (albumId) {
        // For other tracks: loadtracks + jump to index
        console.log("[TrackList] Using playAlbum + jump to index:", index);
        await playerStore.playAlbum(albumId);
        // Small delay to ensure playlist is loaded before jumping
        await new Promise((resolve) => setTimeout(resolve, 100));
        await playerStore.jumpToIndex(index, track);
        await playerStore.loadPlaylist();
        await playerStore.loadStatus();
      } else {
        // Fallback: manual clear + add + jump (for search results, etc.)
        console.log("[TrackList] Fallback: manual add tracks");
        await playerStore.clearPlaylist();

        for (const t of tracks) {
          await playerStore.addToPlaylist(t);
        }

        await playerStore.jumpToIndex(index, track);
        await playerStore.loadPlaylist();
      }
      console.log("[TrackList] handlePlay complete");
    } finally {
      // Always release the lock (even if API calls fail) to keep UI usable.
      isPlayInFlight = false;
      inFlightKey = null;
    }
  }

  async function handleAdd(track: Track, event: MouseEvent) {
    event.stopPropagation();
    console.log("[TrackList] handleAdd called:", {
      title: track.title,
      path: track.path,
      id: track.id,
    });

    if (!track.path) {
      console.error("[TrackList] handleAdd: track.path is missing!", track);
      return;
    }

    await playerStore.addToPlaylist(track);
    await playerStore.loadPlaylist();
    console.log("[TrackList] handleAdd complete");
  }
</script>

{#if tracks.length === 0}
  <div class="flex flex-col items-center justify-center py-12 text-overlay-1">
    <Music2 size={48} class="mb-4 opacity-50" />
    <p class="text-lg">No tracks found</p>
  </div>
{:else}
  <div class="flex flex-col">
    <!-- Album Action Bar -->
    <div
      class="flex items-center justify-between px-5 py-3 border-b border-border/40"
    >
      <div class="flex items-center gap-2">
        <!-- Play Album Button (Primary) -->
        <button
          class="flex items-center gap-1.5 px-4 py-2 rounded-full text-sm font-medium
                 active:scale-95 transition-all shadow-md
                 disabled:opacity-50 disabled:cursor-not-allowed"
          style="background-color: var(--dynamic-accent, var(--color-accent)); color: var(--color-crust);"
          onclick={handlePlayAlbum}
          disabled={isPlayInFlight}
          aria-label="Play album"
        >
          <Play size={16} fill="currentColor" />
          <span>Play</span>
        </button>

        <!-- Shuffle Button -->
        <button
          class="flex items-center gap-1.5 px-3 py-2 rounded-full text-sm text-overlay-1 hover:text-text
                 hover:bg-surface-0 active:scale-95 transition-all
                 disabled:opacity-50 disabled:cursor-not-allowed"
          onclick={handleShuffleAlbum}
          disabled={isPlayInFlight}
          aria-label="Shuffle album"
        >
          <Shuffle size={15} />
          <span>Shuffle</span>
        </button>

        <!-- Add All to Queue Button -->
        <button
          class="flex items-center gap-1.5 px-3 py-2 rounded-full text-sm text-overlay-1 hover:text-text
                 hover:bg-surface-0 active:scale-95 transition-all
                 disabled:opacity-50 disabled:cursor-not-allowed"
          onclick={handleAddAllToQueue}
          disabled={isPlayInFlight}
          aria-label="Add all to queue"
        >
          <ListPlus size={15} />
          <span>Queue All</span>
        </button>
      </div>

      <!-- Album Stats -->
      <div class="text-xs text-overlay-0 font-mono tabular-nums">
        {tracks.length} {tracks.length === 1 ? "track" : "tracks"} · {getTotalDuration()}
      </div>
    </div>

    <!-- Header -->
    <div
      class="grid grid-cols-[auto_1fr_auto] gap-4 px-4 py-1.5 text-[10px] text-overlay-0 uppercase tracking-wider border-b border-border/30"
    >
      <span class="w-6">#</span>
      <span>Title</span>
      <span class="flex items-center">
        <Clock size={11} />
      </span>
    </div>

    <!-- Track list -->
    {#each tracks as track, index}
      <div
        class="group grid grid-cols-[auto_1fr_auto] gap-4 px-4 py-2 hover:bg-surface-0/50 transition-colors text-left items-center cursor-pointer
               {track.id === highlightId ? 'bg-surface-0/40' : ''}"
        style={isPlayInFlight
          ? "pointer-events: none; opacity: 0.7;"
          : undefined}
        onclick={() => handlePlay(track, index)}
        onkeydown={(e) => e.key === "Enter" && handlePlay(track, index)}
        role="button"
        tabindex="0"
      >
        <!-- Track number / Play icon -->
        <div class="w-6 flex items-center justify-center">
          <span class="text-overlay-0 text-xs font-mono tabular-nums group-hover:hidden">
            {track.trackNumber || index + 1}
          </span>
          <Play
            size={13}
            class="hidden group-hover:block"
            style="color: var(--dynamic-accent, var(--color-accent));"
            fill="currentColor"
          />
        </div>

        <!-- Track info -->
        <div class="min-w-0 flex flex-col">
          <span
            class="text-sm text-text truncate {track.id === highlightId
              ? 'dynamic-accent'
              : ''}"
          >
            {track.title}
          </span>
          {#if showArtist || showAlbum}
            <div class="flex items-center gap-1 text-xs text-overlay-0 truncate">
              {#if showArtist}
                <span class="truncate">{track.artist}</span>
              {/if}
              {#if showArtist && showAlbum}
                <span>·</span>
              {/if}
              {#if showAlbum}
                <span class="truncate">{track.album}</span>
              {/if}
            </div>
          {/if}
        </div>

        <!-- Duration & Actions -->
        <div class="flex items-center gap-1.5">
          <button
            class="p-1 rounded text-overlay-0 hover:text-accent opacity-0 group-hover:opacity-100 transition-all"
            onclick={(e) => handleAdd(track, e)}
            aria-label="Add to queue"
          >
            <Plus size={14} />
          </button>
          <span class="text-xs text-overlay-0 w-10 text-right font-mono tabular-nums">
            {formatDuration(track.duration)}
          </span>
        </div>
      </div>
    {/each}
  </div>
{/if}
