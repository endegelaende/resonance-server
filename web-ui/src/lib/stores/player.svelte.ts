/**
 * Player State Store using Svelte 5 Runes
 *
 * This store manages the global player state including:
 * - Current player selection
 * - Playback state (playing, paused, stopped)
 * - Volume
 * - Current track info
 * - Playlist
 * - Smooth elapsed time interpolation between server polls
 *
 * Elapsed-time rendering follows LMS/Jive model:
 * - Server time anchor + local now-delta while playing
 * - Hard reset on track change
 * - No slew/jump heuristics
 */

import { api, type Player, type PlayerStatus, type Track } from "$lib/api";

// =============================================================================
// State
// =============================================================================

// List of all available players
let players = $state<Player[]>([]);

// Currently selected player ID
let selectedPlayerId = $state<string | null>(null);

// Current player status (from server)
let status = $state<PlayerStatus>({
  mode: "stop",
  volume: 50,
  muted: false,
  time: 0,
  duration: 0,
  playlistIndex: 0,
  playlistTracks: 0,
});

// Current track
let currentTrack = $state<Track | null>(null);

// Playlist
let playlist = $state<Track[]>([]);

// Loading states
let isLoading = $state(false);
let isConnected = $state(false);

// Pending action flag - prevents polling from overwriting optimistic UI updates
let pendingAction = $state(false);
let pendingActionTimeout: ReturnType<typeof setTimeout> | null = null;

// Polling intervals
let pollInterval: ReturnType<typeof setInterval> | null = null;
let playerPollInterval: ReturnType<typeof setInterval> | null = null;

// =============================================================================
// Elapsed Time Interpolation (LMS-style)
// =============================================================================
//
// JiveLite/SqueezePlay style model:
// - `anchorElapsed` = server elapsed value
// - `anchorTimestamp` = local timestamp when anchor arrived
// - UI renders `anchorElapsed + (now - anchorTimestamp)` while mode=play

let anchorElapsed = $state(0);
let anchorTimestamp = $state(0);
let displayElapsed = $state(0);
let lastTrackId = $state<number | null>(null);
let animationFrameId: number | null = null;

// Pending volume - when set, prevents polling from overwriting volume
let pendingVolume: number | null = null;
let pendingVolumeTimeout: ReturnType<typeof setTimeout> | null = null;

// Pending seek - when set, prevents polling from overwriting elapsed
let pendingSeek = $state(false);
let pendingSeekTimeout: ReturnType<typeof setTimeout> | null = null;

function resetSmoothing(toElapsed: number): void {
  anchorElapsed = toElapsed;
  anchorTimestamp = performance.now();
  displayElapsed = toElapsed;
}

function updateDisplayElapsed(): void {
  const isCurrentlyPlaying =
    status.mode === "play" || status.mode === "playing";

  if (anchorTimestamp === 0) {
    if (isCurrentlyPlaying) {
      animationFrameId = requestAnimationFrame(updateDisplayElapsed);
    }
    return;
  }

  const now = performance.now();
  const dt = Math.max(0, (now - anchorTimestamp) / 1000);

  let next = anchorElapsed;
  if (isCurrentlyPlaying) {
    next = anchorElapsed + dt;
  }

  const duration = status.duration || 0;
  if (duration > 0) {
    next = Math.min(next, duration);
  }

  displayElapsed = Math.max(0, next);

  if (isCurrentlyPlaying) {
    animationFrameId = requestAnimationFrame(updateDisplayElapsed);
  }
}

function startInterpolation(): void {
  stopInterpolation();
  const isCurrentlyPlaying =
    status.mode === "play" || status.mode === "playing";
  if (isCurrentlyPlaying) {
    animationFrameId = requestAnimationFrame(updateDisplayElapsed);
  }
}

function stopInterpolation(): void {
  if (animationFrameId !== null) {
    cancelAnimationFrame(animationFrameId);
    animationFrameId = null;
  }
}

function syncServerTime(newTime: number, trackId: number | null = null): void {
  const isTrackChange = trackId !== null && trackId !== lastTrackId;
  if (trackId !== null) {
    lastTrackId = trackId;
  }

  if (isTrackChange || anchorTimestamp === 0) {
    resetSmoothing(newTime);
    return;
  }

  anchorElapsed = newTime;
  anchorTimestamp = performance.now();
}

// =============================================================================
// Derived State
// =============================================================================

// Accept both LMS format ("play") and enum format ("playing")
const isPlaying = $derived(status.mode === "play" || status.mode === "playing");
const isPaused = $derived(status.mode === "pause" || status.mode === "paused");
const isStopped = $derived(status.mode === "stop" || status.mode === "stopped");
const hasTrack = $derived(currentTrack !== null);

// Use interpolated elapsed for progress rendering
const elapsedTime = $derived(displayElapsed);
const progress = $derived(
  status.duration > 0 ? (displayElapsed / status.duration) * 100 : 0,
);

const selectedPlayer = $derived(
  players.find((p) => p.id === selectedPlayerId) ?? null,
);

// =============================================================================
// Actions
// =============================================================================

async function loadPlayers(): Promise<void> {
  try {
    isLoading = true;
    const result = await api.getServerStatus();
    players = result.players;
    isConnected = true;

    // Prefer real audio players; fall back to any connected player.
    const hasSelectedPlayer =
      selectedPlayerId !== null && players.some((p) => p.id === selectedPlayerId);

    if (!hasSelectedPlayer && players.length > 0) {
      const preferredPlayer =
        players.find((p) => p.connected && p.isPlayer) ??
        players.find((p) => p.connected) ??
        players[0];
      selectedPlayerId = preferredPlayer?.id ?? null;
    }
  } catch (error) {
    console.error("Failed to load players:", error);
    isConnected = false;
  } finally {
    isLoading = false;
  }
}

async function loadStatus(): Promise<void> {
  if (!selectedPlayerId) return;

  // Skip polling while an action is pending to avoid race conditions
  if (pendingAction) {
    console.log("[loadStatus] SKIPPED - pendingAction is true");
    return;
  }

  // Skip polling while a seek is pending
  if (pendingSeek) {
    console.log("[loadStatus] SKIPPED - pendingSeek is true");
    return;
  }

  console.log("[loadStatus] Fetching status...");

  try {
    const newStatus = await api.getPlayerStatus(selectedPlayerId);
    console.log("[loadStatus] Server returned mode:", newStatus.mode);
    const wasPlaying = status.mode === "play" || status.mode === "playing";
    const nowPlaying =
      newStatus.mode === "play" || newStatus.mode === "playing";

    // Check if track changed - if so, load BlurHash for new track
    const newTrack = newStatus.currentTrack ?? null;
    const trackChanged = newTrack?.id !== currentTrack?.id;
    const trackId = newTrack?.id ?? null;

    // Sync server time with track change detection
    syncServerTime(newStatus.time || 0, trackId);

    // Preserve pending volume if set (user is adjusting volume)
    const preservedVolume =
      pendingVolume !== null ? pendingVolume : newStatus.volume;

    status = newStatus;
    status.volume = preservedVolume;

    currentTrack = newTrack;
    isConnected = true;

    // Load BlurHash for new track (non-blocking)
    if (trackChanged && newTrack?.id) {
      loadBlurHashForTrack(newTrack.id);
    }

    // Start/stop interpolation based on play state change
    if (nowPlaying && !wasPlaying) {
      startInterpolation();
    } else if (!nowPlaying && wasPlaying) {
      stopInterpolation();
    }
  } catch (error) {
    console.error("Failed to load player status:", error);
    isConnected = false;
  }
}

/**
 * Load BlurHash for a track and update currentTrack.
 * This is a non-blocking operation that runs in the background.
 */
async function loadBlurHashForTrack(trackId: number): Promise<void> {
  try {
    const blurhash = await api.getTrackBlurHash(trackId);
    // Only update if this is still the current track
    if (currentTrack?.id === trackId && blurhash) {
      currentTrack = { ...currentTrack, blurhash };
    }
  } catch (error) {
    // BlurHash loading failure is not critical - silently ignore
    console.debug("Failed to load BlurHash for track:", trackId, error);
  }
}

async function loadPlaylist(): Promise<void> {
  if (!selectedPlayerId) return;

  try {
    const result = await api.getPlaylist(selectedPlayerId, 0, 100);
    playlist = result.tracks;
  } catch (error) {
    console.error("Failed to load playlist:", error);
  }
}

function selectPlayer(playerId: string): void {
  selectedPlayerId = playerId;
  stopInterpolation();
  resetSmoothing(0);
  lastTrackId = null;
  loadStatus();
  loadPlaylist();
}

/**
 * Set pending action flag to prevent polling from overwriting UI state.
 * Automatically clears after a timeout.
 * @param timeoutMs - How long to block polling (default 500ms, use longer for complex operations)
 */
function setPendingAction(timeoutMs = 500): void {
  console.log(
    "[setPendingAction] Setting pendingAction = true, timeout =",
    timeoutMs,
  );
  pendingAction = true;
  if (pendingActionTimeout) {
    clearTimeout(pendingActionTimeout);
  }
  // Clear pending flag after timeout - enough time for the action to complete
  pendingActionTimeout = setTimeout(() => {
    console.log("[setPendingAction] Timeout expired, pendingAction = false");
    pendingAction = false;
    pendingActionTimeout = null;
    // Force a status refresh after pending clears to sync with server
    loadStatus();
  }, timeoutMs);
}

/**
 * Set pending seek flag to prevent polling from overwriting elapsed time.
 * Automatically clears after a timeout.
 */
function setPendingSeek(timeoutMs = 1000): void {
  console.log(
    "[setPendingSeek] Setting pendingSeek = true, timeout =",
    timeoutMs,
  );
  pendingSeek = true;
  if (pendingSeekTimeout) {
    clearTimeout(pendingSeekTimeout);
  }
  pendingSeekTimeout = setTimeout(() => {
    console.log("[setPendingSeek] Timeout expired, pendingSeek = false");
    pendingSeek = false;
    pendingSeekTimeout = null;
  }, timeoutMs);
}

async function play(): Promise<void> {
  if (!selectedPlayerId) return;

  console.log("[play] Called, setting pendingAction");
  setPendingAction();

  // Optimistic UI update
  status.mode = "play";
  resetSmoothing(displayElapsed);
  startInterpolation();

  // Server is now LMS-like: 'play' from STOP with a non-empty queue starts the current playlist item.
  console.log("[play] Sending API play request...");
  await api.play(selectedPlayerId);
  console.log("[play] API play request completed");
}

async function pause(): Promise<void> {
  if (!selectedPlayerId) return;
  console.log("[pause] Called, setting pendingAction");
  setPendingAction();
  status.mode = "pause";
  stopInterpolation();
  // Keep current display elapsed as anchor
  anchorElapsed = displayElapsed;
  console.log("[pause] Sending API pause request...");
  await api.pause(selectedPlayerId);
  console.log("[pause] API pause request completed");
}

async function stop(): Promise<void> {
  if (!selectedPlayerId) return;
  setPendingAction();
  status.mode = "stop";
  stopInterpolation();
  // LMS-like: keep showing last position, don't reset to 0
  await api.stop(selectedPlayerId);
}

async function togglePlayPause(): Promise<void> {
  console.log("[togglePlayPause] Called, isPlaying =", isPlaying);
  if (isPlaying) {
    await pause();
  } else {
    await play();
  }
}

async function next(): Promise<void> {
  if (!selectedPlayerId) return;
  setPendingAction();
  stopInterpolation();
  resetSmoothing(0);
  await api.next(selectedPlayerId);
  // Wait a bit for the server to process, then fetch new status
  setTimeout(() => {
    pendingAction = false;
    loadStatus();
  }, 300);
}

async function previous(): Promise<void> {
  if (!selectedPlayerId) return;
  setPendingAction();
  stopInterpolation();
  resetSmoothing(0);
  await api.previous(selectedPlayerId);
  // Wait a bit for the server to process, then fetch new status
  setTimeout(() => {
    pendingAction = false;
    loadStatus();
  }, 300);
}

async function jumpToIndex(index: number, track?: Track): Promise<void> {
  if (!selectedPlayerId) return;
  setPendingAction();
  stopInterpolation();
  resetSmoothing(0);

  // Optimistic update: set currentTrack immediately if provided
  if (track) {
    currentTrack = track;
    status.mode = "play";
    status.duration = track.duration || 0;
    status.playlistIndex = index;
    startInterpolation();
  }

  await api.jumpToIndex(selectedPlayerId, index);
  // Wait a bit for the server to process, then fetch new status
  setTimeout(() => {
    pendingAction = false;
    loadStatus();
  }, 300);
}

async function seek(seconds: number): Promise<void> {
  if (!selectedPlayerId) return;

  console.log("[seek] Called, target:", seconds);

  // Set pending seek to prevent polling from reverting our optimistic update
  setPendingSeek(1500);

  // Immediately update display elapsed for responsiveness (like Cadence)
  resetSmoothing(seconds);

  // Restart interpolation if playing
  const isCurrentlyPlaying =
    status.mode === "play" || status.mode === "playing";
  if (isCurrentlyPlaying) {
    startInterpolation();
  }

  console.log("[seek] Sending API seek request...");
  await api.seek(selectedPlayerId, seconds);
  console.log("[seek] API seek request completed");
}

async function setVolume(volume: number): Promise<void> {
  if (!selectedPlayerId) return;

  // Set pending volume to prevent polling from overwriting
  pendingVolume = volume;
  status.volume = volume;

  // Clear any existing timeout
  if (pendingVolumeTimeout) {
    clearTimeout(pendingVolumeTimeout);
  }

  // Clear pending after 2 seconds (enough for server to sync)
  pendingVolumeTimeout = setTimeout(() => {
    pendingVolume = null;
    pendingVolumeTimeout = null;
  }, 2000);

  await api.setVolume(selectedPlayerId, volume);
}

async function adjustVolume(delta: number): Promise<void> {
  if (!selectedPlayerId) return;
  const newVolume = Math.max(0, Math.min(100, status.volume + delta));

  // Set pending volume to prevent polling from overwriting
  pendingVolume = newVolume;
  status.volume = newVolume;

  // Clear any existing timeout
  if (pendingVolumeTimeout) {
    clearTimeout(pendingVolumeTimeout);
  }

  // Clear pending after 2 seconds
  pendingVolumeTimeout = setTimeout(() => {
    pendingVolume = null;
    pendingVolumeTimeout = null;
  }, 2000);

  await api.adjustVolume(selectedPlayerId, delta);
}

async function toggleMute(): Promise<void> {
  if (!selectedPlayerId) return;
  await api.toggleMute(selectedPlayerId);
  status.muted = !status.muted;
}

async function playTrack(track: Track): Promise<void> {
  console.log("[playerStore] playTrack called:", {
    title: track.title,
    path: track.path,
    playerId: selectedPlayerId,
  });
  if (!selectedPlayerId) {
    console.log(
      "[playerStore] playTrack: no selectedPlayerId, returning early",
    );
    return;
  }

  // Set pending action to prevent polling from overwriting our optimistic update
  // Use a longer timeout (2s) since playTrack is followed by batch adds of remaining tracks
  // which can take time depending on album size
  setPendingAction(2000);

  // Optimistic update BEFORE the API call for immediate UI feedback
  currentTrack = track;
  status.mode = "play";
  status.duration = track.duration || 0;
  // Reset time and start interpolation
  resetSmoothing(0);
  startInterpolation();

  console.log("[playerStore] playTrack: calling api.playTrack...");
  await api.playTrack(selectedPlayerId, track.path);
  console.log("[playerStore] playTrack: api.playTrack returned");

  // Don't call loadPlaylist here - TrackList.svelte will add remaining tracks
  // and call loadPlaylist after each add. We just need to wait for pending to clear.
}

async function playAlbum(albumId: string): Promise<void> {
  if (!selectedPlayerId) return;
  await api.playAlbum(selectedPlayerId, albumId);
  await loadStatus();
  await loadPlaylist();
}

async function addToPlaylist(track: Track): Promise<void> {
  if (!selectedPlayerId) return;
  await api.addTrack(selectedPlayerId, track.path);
  // Don't reload playlist after every add - this causes race conditions
  // and unnecessary network traffic. The playlist will be loaded after
  // pending action clears or on next poll cycle.
}

async function clearPlaylist(): Promise<void> {
  if (!selectedPlayerId) return;

  // Optimistic UI update: clearing the playlist should stop playback and clear Now Playing.
  // Prevent the polling loop from briefly re-hydrating stale "currentTrack" / mode.
  setPendingAction(1000);
  stopInterpolation();
  resetSmoothing(0);

  status.mode = "stop";
  status.time = 0;
  status.duration = 0;
  status.playlistIndex = 0;
  status.playlistTracks = 0;
  currentTrack = null;
  playlist = [];

  await api.clearPlaylist(selectedPlayerId);

  // Ensure we converge to server truth after the clear has been processed
  setTimeout(() => {
    pendingAction = false;
    loadStatus();
    loadPlaylist();
  }, 300);
}

// =============================================================================
// Polling
// =============================================================================

function startPolling(intervalMs = 1000): void {
  stopPolling();

  // Poll for player status updates
  pollInterval = setInterval(() => {
    loadStatus();
  }, intervalMs);

  // Poll for player list updates (new players connecting/disconnecting)
  playerPollInterval = setInterval(() => {
    loadPlayers();
  }, 5000); // Every 5 seconds
}

function stopPolling(): void {
  if (pollInterval) {
    clearInterval(pollInterval);
    pollInterval = null;
  }
  if (playerPollInterval) {
    clearInterval(playerPollInterval);
    playerPollInterval = null;
  }
  stopInterpolation();
}

// =============================================================================
// Initialize
// =============================================================================

async function initialize(): Promise<void> {
  await loadPlayers();
  await loadStatus();
  await loadPlaylist();
  startPolling();
  // Start interpolation if already playing
  const isCurrentlyPlaying =
    status.mode === "play" || status.mode === "playing";
  if (isCurrentlyPlaying) {
    startInterpolation();
  }
}

// =============================================================================
// Export
// =============================================================================

export const playerStore = {
  // State (getters)
  get players() {
    return players;
  },
  get selectedPlayerId() {
    return selectedPlayerId;
  },
  get selectedPlayer() {
    return selectedPlayer;
  },
  get status() {
    return status;
  },
  get currentTrack() {
    return currentTrack;
  },
  get playlist() {
    return playlist;
  },
  get isLoading() {
    return isLoading;
  },
  get isConnected() {
    return isConnected;
  },

  // Derived (getters)
  get isPlaying() {
    return isPlaying;
  },
  get isPaused() {
    return isPaused;
  },
  get isStopped() {
    return isStopped;
  },
  get hasTrack() {
    return hasTrack;
  },
  get progress() {
    return progress;
  },
  get elapsedTime() {
    return elapsedTime;
  },

  // Actions
  initialize,
  loadPlayers,
  loadStatus,
  loadPlaylist,
  selectPlayer,
  play,
  pause,
  stop,
  togglePlayPause,
  next,
  previous,
  jumpToIndex,
  seek,
  setVolume,
  adjustVolume,
  toggleMute,
  playTrack,
  playAlbum,
  addToPlaylist,
  clearPlaylist,
  startPolling,
  stopPolling,
};

