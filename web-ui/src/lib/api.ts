/**
 * Resonance API Client
 *
 * TypeScript client for communicating with the Resonance backend.
 * Supports JSON-RPC (LMS-compatible) and REST API endpoints.
 */

// =============================================================================
// Types
// =============================================================================

export interface Player {
  id: string;
  name: string;
  model: string;
  connected: boolean;
  isPlayer: boolean;
  isPlaying: boolean;
  volume: number;
  muted: boolean;
  elapsed: number;
  duration: number;
  playlistIndex: number;
  playlistTracks: number;
}

export interface Track {
  id: number;
  title: string;
  artist: string;
  album: string;
  albumArtist?: string;
  duration: number;
  trackNumber?: number;
  discNumber?: number;
  year?: number;
  genre?: string;
  path: string;
  coverArt?: string;
  // Audio quality metadata
  sampleRate?: number;
  bitDepth?: number;
  bitrate?: number;
  channels?: number;
  format?: string;
  // BlurHash placeholder for instant preview
  blurhash?: string;
}

export interface Album {
  id: string;
  name: string;
  artist: string;
  year?: number;
  trackCount: number;
  coverArt?: string;
}

export interface Artist {
  id: string;
  name: string;
  albumCount: number;
}

export interface PlayerStatus {
  mode: string;
  volume: number;
  muted: boolean;
  time: number;
  duration: number;
  currentTrack?: Track;
  playlistIndex: number;
  playlistTracks: number;
}

export interface SearchResults {
  artists: Artist[];
  albums: Album[];
  tracks: Track[];
}

// MusicFolder is just a string path (backend returns string array)
export type MusicFolder = string;

export interface ScanStatus {
  scanning: boolean;
  progress: number;
  current_folder: string | null;
  folders_total: number;
  folders_done: number;
  tracks_found: number;
  errors: string[];
}

export interface PlayerRuntimePrefs {
  transitionType: string;
  transitionDuration: string;
  transitionSmart: string;
  replayGainMode: string;
  remoteReplayGain: string;
  gapless: string;
}

export interface SyncGroup {
  members: string[];
  memberNames: string[];
}

export interface AlarmEntry {
  id: string;
  time: number;
  dow: number[];
  enabled: boolean;
  repeat: boolean;
  volume: number;
  shufflemode: number;
  url: string;
}

export interface AlarmUpdateInput {
  timeSeconds?: number;
  dow?: number[];
  enabled?: boolean;
  repeat?: boolean;
  volume?: number;
  shufflemode?: number;
  url?: string;
}

// JSON-RPC types
interface JsonRpcRequest {
  id: number;
  method: string;
  params: [string, string[]];
}

interface JsonRpcResponse<T = unknown> {
  id: number;
  method: string;
  params: [string, string[]];
  result?: T;
  error?: { code: number; message: string };
}

// =============================================================================
// API Client
// =============================================================================

class ResonanceAPI {
  private baseUrl: string;
  private requestId = 0;

  constructor(baseUrl = "") {
    this.baseUrl = baseUrl;
  }

  // ---------------------------------------------------------------------------
  // JSON-RPC Helper
  // ---------------------------------------------------------------------------

  private async rpc<T>(playerId: string, command: string[]): Promise<T> {
    const request: JsonRpcRequest = {
      id: ++this.requestId,
      method: "slim.request",
      params: [playerId || "-", command],
    };

    const requestBody = JSON.stringify(request);
    console.log("[api.rpc] Sending request:", {
      id: request.id,
      playerId,
      command,
      bodyLength: requestBody.length,
    });

    const response = await fetch(`${this.baseUrl}/jsonrpc.js`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: requestBody,
    });

    console.log(
      "[api.rpc] Response status:",
      response.status,
      "for command:",
      command[0],
      command[1] || "",
    );

    if (!response.ok) {
      console.error("[api.rpc] HTTP error:", response.status);
      throw new Error(`HTTP error: ${response.status}`);
    }

    const data: JsonRpcResponse<T> = await response.json();
    console.log("[api.rpc] Response data:", {
      id: data.id,
      hasResult: !!data.result,
      hasError: !!data.error,
    });

    if (data.error) {
      console.error("[api.rpc] RPC error:", data.error);
      throw new Error(data.error.message);
    }

    return data.result as T;
  }

  // ---------------------------------------------------------------------------
  // Server
  // ---------------------------------------------------------------------------

  async getServerStatus(): Promise<{
    version: string;
    uuid: string;
    playerCount: number;
    players: Player[];
  }> {
    const result = await this.rpc<{
      version: string;
      uuid: string;
      "player count": number;
      players_loop: Array<{
        playerid: string;
        name: string;
        model: string;
        connected: number;
        isplayer?: number;
        isplaying: number;
        "mixer volume": number;
      }>;
    }>("-", ["serverstatus", "0", "100"]);

    const players = (result.players_loop || []).map((p) => ({
      id: p.playerid,
      name: p.name,
      model: p.model,
      connected: p.connected === 1,
      isPlayer: p.isplayer !== 0,
      isPlaying: p.isplaying === 1,
      volume: p["mixer volume"] || 50,
      muted: false,
      elapsed: 0,
      duration: 0,
      playlistIndex: 0,
      playlistTracks: 0,
    }));

    const audioPlayers = players.filter((player) => player.isPlayer);
    const visiblePlayers = audioPlayers.length > 0 ? audioPlayers : players;

    return {
      version: result.version,
      uuid: result.uuid,
      playerCount: visiblePlayers.length,
      players: visiblePlayers,
    };
  }

  // ---------------------------------------------------------------------------
  // Players
  // ---------------------------------------------------------------------------

  async getPlayers(): Promise<Player[]> {
    const status = await this.getServerStatus();
    return status.players;
  }

  async getPlayerStatus(playerId: string): Promise<PlayerStatus> {
    // Use "-" as start to get current track (LMS convention)
    const result = await this.rpc<{
      mode: string;
      "mixer volume": number;
      time: number;
      duration: number;
      // Backend may return either of these (we prefer "playlist index" if present)
      playlist_cur_index?: number;
      "playlist index"?: number;
      playlist_tracks: number;
      // Preferred: explicit currentTrack object from backend (stable)
      currentTrack?: Track;
      // Fallback: LMS-style playlist_loop
      playlist_loop?: Array<{
        id: number;
        title: string;
        artist: string;
        album: string;
        duration: number;
        url?: string;
        artwork_url?: string;
        coverArt?: string;
      }>;
    }>(playerId, ["status", "-", "1", "tags:aAdlKkt"]);

    const currentTrackFromLoop = result.playlist_loop?.[0];
    const playlistIndex =
      result["playlist index"] ?? result.playlist_cur_index ?? 0;

    return {
      mode: result.mode || "stop",
      volume: result["mixer volume"] || 50,
      muted: result["mixer volume"] === 0,
      time: result.time || 0,
      duration: result.duration || 0,
      currentTrack: result.currentTrack
        ? result.currentTrack
        : currentTrackFromLoop
          ? {
              id: currentTrackFromLoop.id,
              title: currentTrackFromLoop.title,
              artist: currentTrackFromLoop.artist,
              album: currentTrackFromLoop.album,
              duration: currentTrackFromLoop.duration,
              // NOTE: LMS `url` is a stream URL, not a filesystem path. Keep it for now as fallback.
              path: currentTrackFromLoop.url || "",
              coverArt:
                currentTrackFromLoop.coverArt ||
                currentTrackFromLoop.artwork_url,
            }
          : undefined,
      playlistIndex,
      playlistTracks: result.playlist_tracks || 0,
    };
  }

  // ---------------------------------------------------------------------------
  // Playback Control
  // ---------------------------------------------------------------------------

  async play(playerId: string): Promise<void> {
    await this.rpc(playerId, ["play"]);
  }

  async pause(playerId: string): Promise<void> {
    await this.rpc(playerId, ["pause"]);
  }

  async stop(playerId: string): Promise<void> {
    await this.rpc(playerId, ["stop"]);
  }

  async togglePlayPause(playerId: string): Promise<void> {
    await this.rpc(playerId, ["pause"]);
  }

  async next(playerId: string): Promise<void> {
    await this.rpc(playerId, ["playlist", "jump", "+1"]);
  }

  async previous(playerId: string): Promise<void> {
    await this.rpc(playerId, ["playlist", "jump", "-1"]);
  }

  async jumpToIndex(playerId: string, index: number): Promise<void> {
    await this.rpc(playerId, ["playlist", "index", index.toString()]);
  }

  async seek(playerId: string, seconds: number): Promise<void> {
    await this.rpc(playerId, ["time", seconds.toString()]);
  }

  async setVolume(playerId: string, volume: number): Promise<void> {
    await this.rpc(playerId, ["mixer", "volume", volume.toString()]);
  }

  async adjustVolume(playerId: string, delta: number): Promise<void> {
    const sign = delta >= 0 ? "+" : "";
    await this.rpc(playerId, ["mixer", "volume", `${sign}${delta}`]);
  }

  async toggleMute(playerId: string): Promise<void> {
    await this.rpc(playerId, ["mixer", "muting", "toggle"]);
  }

  // ---------------------------------------------------------------------------
  // Runtime Player Preferences
  // ---------------------------------------------------------------------------

  async getPlayerPref(playerId: string, prefName: string): Promise<string> {
    const result = await this.rpc<{ _p2?: string }>(playerId, [
      "playerpref",
      prefName,
      "?",
    ]);
    return result._p2 ?? "";
  }

  async setPlayerPref(
    playerId: string,
    prefName: string,
    prefValue: string | number | boolean,
  ): Promise<string> {
    const value =
      typeof prefValue === "boolean" ? (prefValue ? "1" : "0") : String(prefValue);
    const result = await this.rpc<{ _p2?: string }>(playerId, [
      "playerpref",
      prefName,
      value,
    ]);
    return result._p2 ?? value;
  }

  async getRuntimePrefs(playerId: string): Promise<PlayerRuntimePrefs> {
    const prefNames = [
      "transitionType",
      "transitionDuration",
      "transitionSmart",
      "replayGainMode",
      "remoteReplayGain",
      "gapless",
    ] as const;

    const values = await Promise.all(
      prefNames.map((name) => this.getPlayerPref(playerId, name)),
    );

    return {
      transitionType: values[0],
      transitionDuration: values[1],
      transitionSmart: values[2],
      replayGainMode: values[3],
      remoteReplayGain: values[4],
      gapless: values[5],
    };
  }

  async setRuntimePrefs(
    playerId: string,
    prefs: PlayerRuntimePrefs,
  ): Promise<PlayerRuntimePrefs> {
    const [transitionType, transitionDuration, transitionSmart, replayGainMode, remoteReplayGain, gapless] =
      await Promise.all([
        this.setPlayerPref(playerId, "transitionType", prefs.transitionType),
        this.setPlayerPref(playerId, "transitionDuration", prefs.transitionDuration),
        this.setPlayerPref(playerId, "transitionSmart", prefs.transitionSmart),
        this.setPlayerPref(playerId, "replayGainMode", prefs.replayGainMode),
        this.setPlayerPref(playerId, "remoteReplayGain", prefs.remoteReplayGain),
        this.setPlayerPref(playerId, "gapless", prefs.gapless),
      ]);

    return {
      transitionType,
      transitionDuration,
      transitionSmart,
      replayGainMode,
      remoteReplayGain,
      gapless,
    };
  }

  // ---------------------------------------------------------------------------
  // Sync / Multiroom
  // ---------------------------------------------------------------------------

  async getSyncBuddies(playerId: string): Promise<string[]> {
    const result = await this.rpc<{ _sync?: string }>(playerId, ["sync", "?"]);
    const raw = result._sync?.trim() ?? "-";
    if (!raw || raw === "-") {
      return [];
    }
    return raw
      .split(",")
      .map((entry) => entry.trim())
      .filter((entry) => entry.length > 0);
  }

  async syncPlayer(playerId: string, targetPlayerId: string): Promise<void> {
    await this.rpc(playerId, ["sync", targetPlayerId]);
  }

  async unsyncPlayer(playerId: string): Promise<void> {
    await this.rpc(playerId, ["sync", "-"]);
  }

  async getSyncGroups(): Promise<SyncGroup[]> {
    const result = await this.rpc<{
      syncgroups_loop?: Array<{
        sync_members?: string;
        sync_member_names?: string;
      }>;
    }>("-", ["syncgroups", "?"]);

    return (result.syncgroups_loop || []).map((group) => ({
      members: (group.sync_members || "")
        .split(",")
        .map((entry) => entry.trim())
        .filter((entry) => entry.length > 0),
      memberNames: (group.sync_member_names || "")
        .split(",")
        .map((entry) => entry.trim())
        .filter((entry) => entry.length > 0),
    }));
  }

  // ---------------------------------------------------------------------------
  // Alarms
  // ---------------------------------------------------------------------------

  private buildAlarmUpdateArgs(update: AlarmUpdateInput): string[] {
    const args: string[] = [];

    if (update.timeSeconds !== undefined) {
      args.push(`time:${Math.max(0, Math.floor(update.timeSeconds))}`);
    }

    if (update.dow !== undefined) {
      const normalized = Array.from(
        new Set(
          update.dow
            .map((day) => Math.floor(day))
            .filter((day) => day >= 0 && day <= 6),
        ),
      ).sort((a, b) => a - b);
      args.push(`dow:${normalized.join(",")}`);
    }

    if (update.enabled !== undefined) {
      args.push(`enabled:${update.enabled ? "1" : "0"}`);
    }

    if (update.repeat !== undefined) {
      args.push(`repeat:${update.repeat ? "1" : "0"}`);
    }

    if (update.volume !== undefined) {
      args.push(`volume:${Math.max(0, Math.min(100, Math.floor(update.volume)))}`);
    }

    if (update.shufflemode !== undefined) {
      args.push(`shufflemode:${Math.max(0, Math.floor(update.shufflemode))}`);
    }

    if (update.url !== undefined) {
      args.push(`url:${update.url}`);
    }

    return args;
  }

  async getAlarms(playerId: string): Promise<AlarmEntry[]> {
    const result = await this.rpc<{
      alarms_loop?: Array<{
        id?: string;
        time?: number;
        dow?: string;
        enabled?: number;
        repeat?: number;
        volume?: number;
        shufflemode?: number;
        url?: string;
      }>;
    }>(playerId, ["alarms", "0", "200", "filter:all"]);

    return (result.alarms_loop || []).map((alarm) => {
      const rawDow = (alarm.dow || "")
        .split(",")
        .map((part) => Number.parseInt(part.trim(), 10))
        .filter((day) => Number.isFinite(day) && day >= 0 && day <= 6);

      return {
        id: alarm.id || "",
        time: Number.isFinite(alarm.time) ? Math.max(0, Math.floor(alarm.time || 0)) : 0,
        dow: Array.from(new Set(rawDow)).sort((a, b) => a - b),
        enabled: (alarm.enabled || 0) !== 0,
        repeat: (alarm.repeat || 0) !== 0,
        volume: Number.isFinite(alarm.volume) ? Math.max(0, Math.floor(alarm.volume || 0)) : 50,
        shufflemode: Number.isFinite(alarm.shufflemode)
          ? Math.max(0, Math.floor(alarm.shufflemode || 0))
          : 0,
        url: alarm.url || "CURRENT_PLAYLIST",
      };
    });
  }

  async addAlarm(
    playerId: string,
    alarm: AlarmUpdateInput & { timeSeconds: number },
  ): Promise<string | undefined> {
    const cmd = ["alarm", "add", ...this.buildAlarmUpdateArgs(alarm)];
    const result = await this.rpc<{ id?: string }>(playerId, cmd);
    return result.id;
  }

  async updateAlarm(
    playerId: string,
    alarmId: string,
    update: AlarmUpdateInput,
  ): Promise<void> {
    const cmd = [
      "alarm",
      "update",
      `id:${alarmId}`,
      ...this.buildAlarmUpdateArgs(update),
    ];
    await this.rpc(playerId, cmd);
  }

  async deleteAlarm(playerId: string, alarmId: string): Promise<void> {
    await this.rpc(playerId, ["alarm", "delete", `id:${alarmId}`]);
  }

  async enableAllAlarms(playerId: string): Promise<void> {
    await this.rpc(playerId, ["alarm", "enableall"]);
  }

  async disableAllAlarms(playerId: string): Promise<void> {
    await this.rpc(playerId, ["alarm", "disableall"]);
  }

  async setDefaultAlarmVolume(playerId: string, volume: number): Promise<number> {
    const sanitized = Math.max(0, Math.min(100, Math.floor(volume)));
    const result = await this.rpc<{ volume?: number }>(playerId, [
      "alarm",
      "defaultvolume",
      `volume:${sanitized}`,
    ]);
    return Number.isFinite(result.volume) ? Math.max(0, Math.floor(result.volume || 0)) : sanitized;
  }

  // ---------------------------------------------------------------------------
  // Playlist
  // ---------------------------------------------------------------------------

  async playTrack(playerId: string, trackPath: string): Promise<void> {
    console.log("[api] playTrack called:", { playerId, trackPath });
    await this.rpc(playerId, ["playlist", "play", trackPath]);
    console.log("[api] playTrack rpc returned");
  }

  async playAlbum(playerId: string, albumId: string): Promise<void> {
    await this.rpc(playerId, [
      "playlist",
      "loadtracks",
      `album_id:${albumId}`,
      "sort:tracknum",
    ]);
  }

  async addTrack(playerId: string, trackPath: string): Promise<void> {
    await this.rpc(playerId, ["playlist", "add", trackPath]);
  }

  async insertTrack(playerId: string, trackPath: string): Promise<void> {
    await this.rpc(playerId, ["playlist", "insert", trackPath]);
  }

  async clearPlaylist(playerId: string): Promise<void> {
    await this.rpc(playerId, ["playlist", "clear"]);
  }

  async getPlaylist(
    playerId: string,
    start = 0,
    count = 50,
  ): Promise<{ tracks: Track[]; total: number }> {
    const result = await this.rpc<{
      playlist_loop: Array<{
        id: number;
        title: string;
        artist: string;
        album: string;
        duration: number;
        url: string;
        coverArt?: string;
        artwork_url?: string;
      }>;
      count: number;
    }>(playerId, ["status", start.toString(), count.toString(), "tags:aAdlt"]);

    return {
      tracks: (result.playlist_loop || []).map((t) => ({
        id: t.id,
        title: t.title,
        artist: t.artist,
        album: t.album,
        duration: t.duration,
        path: t.url,
        coverArt: t.coverArt || t.artwork_url,
      })),
      total: result.count || 0,
    };
  }

  // ---------------------------------------------------------------------------
  // Library
  // ---------------------------------------------------------------------------

  async getArtists(
    start = 0,
    count = 50,
  ): Promise<{ artists: Artist[]; total: number }> {
    const result = await this.rpc<{
      artists_loop: Array<{ id: string; artist: string; albums: number }>;
      count: number;
    }>("-", ["artists", start.toString(), count.toString()]);

    return {
      artists: (result.artists_loop || []).map((a) => ({
        id: a.id,
        name: a.artist,
        albumCount: a.albums || 0,
      })),
      total: result.count || 0,
    };
  }

  async getAlbums(
    start = 0,
    count = 50,
    artistId?: string,
  ): Promise<{ albums: Album[]; total: number }> {
    const cmd = ["albums", start.toString(), count.toString(), "tags:lyj"];
    if (artistId) {
      cmd.push(`artist_id:${artistId}`);
    }

    const result = await this.rpc<{
      albums_loop: Array<{
        id: string;
        album: string;
        artist: string;
        year?: number;
        tracks: number;
        artwork_url?: string;
      }>;
      count: number;
    }>("-", cmd);

    return {
      albums: (result.albums_loop || []).map((a) => ({
        id: a.id,
        name: a.album,
        artist: a.artist,
        year: a.year,
        trackCount: a.tracks || 0,
        coverArt: a.artwork_url,
      })),
      total: result.count || 0,
    };
  }

  async getTracks(
    start = 0,
    count = 50,
    albumId?: string,
  ): Promise<{ tracks: Track[]; total: number }> {
    const cmd = ["titles", start.toString(), count.toString(), "tags:aAdltyKn"];
    if (albumId) {
      cmd.push(`album_id:${albumId}`);
    }

    const result = await this.rpc<{
      titles_loop: Array<{
        id: number;
        title: string;
        artist: string;
        album: string;
        albumartist?: string;
        duration: number;
        tracknum?: number;
        year?: number;
        url: string;
        artwork_url?: string;
      }>;
      count: number;
    }>("-", cmd);

    return {
      tracks: (result.titles_loop || []).map((t) => ({
        id: t.id,
        title: t.title,
        artist: t.artist,
        album: t.album,
        albumArtist: t.albumartist,
        duration: t.duration,
        trackNumber: t.tracknum,
        year: t.year,
        path: t.url,
        coverArt: t.artwork_url,
      })),
      total: result.count || 0,
    };
  }

  async search(query: string): Promise<SearchResults> {
    const result = await this.rpc<{
      artists_loop?: Array<{ id: string; artist: string }>;
      albums_loop?: Array<{ id: string; album: string; artist: string }>;
      titles_loop?: Array<{
        id: number;
        title: string;
        artist: string;
        album: string;
        duration: number;
        url: string;
      }>;
    }>("-", ["search", "0", "20", `term:${query}`]);

    return {
      artists: (result.artists_loop || []).map((a) => ({
        id: a.id,
        name: a.artist,
        albumCount: 0,
      })),
      albums: (result.albums_loop || []).map((a) => ({
        id: a.id,
        name: a.album,
        artist: a.artist,
        trackCount: 0,
        coverArt: (a as any).artwork_url,
      })),
      tracks: (result.titles_loop || []).map((t) => ({
        id: t.id,
        title: t.title,
        artist: t.artist,
        album: t.album,
        duration: t.duration,
        path: (t as any).url,
        coverArt: (t as any).artwork_url,
      })),
    };
  }

  // ---------------------------------------------------------------------------
  // Library Management
  // ---------------------------------------------------------------------------

  async rescan(): Promise<void> {
    await this.rpc("-", ["rescan"]);
  }

  async wipecache(): Promise<void> {
    await this.rpc("-", ["wipecache"]);
  }

  // ---------------------------------------------------------------------------
  // Music Folders (REST API)
  // ---------------------------------------------------------------------------

  async getMusicFolders(): Promise<MusicFolder[]> {
    const response = await fetch(`${this.baseUrl}/api/library/folders`);
    if (!response.ok) {
      throw new Error(`HTTP error: ${response.status}`);
    }
    const data = await response.json();
    // Backend returns string array directly
    return data.folders || [];
  }

  async addMusicFolder(path: string): Promise<MusicFolder[]> {
    const response = await fetch(`${this.baseUrl}/api/library/folders`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!response.ok) {
      const error = await response
        .json()
        .catch(() => ({ detail: "Unknown error" }));
      throw new Error(error.detail || `HTTP error: ${response.status}`);
    }
    const data = await response.json();
    return data.folders || [];
  }

  async removeMusicFolder(path: string): Promise<MusicFolder[]> {
    const response = await fetch(`${this.baseUrl}/api/library/folders`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!response.ok) {
      const error = await response
        .json()
        .catch(() => ({ detail: "Unknown error" }));
      throw new Error(error.detail || `HTTP error: ${response.status}`);
    }
    const data = await response.json();
    return data.folders || [];
  }

  // ---------------------------------------------------------------------------
  // Library Scan (REST API)
  // ---------------------------------------------------------------------------

  async startScan(): Promise<{ status: string; scanning: boolean }> {
    const response = await fetch(`${this.baseUrl}/api/library/scan`, {
      method: "POST",
    });
    if (!response.ok) {
      const error = await response
        .json()
        .catch(() => ({ detail: "Unknown error" }));
      throw new Error(error.detail || `HTTP error: ${response.status}`);
    }
    return response.json();
  }

  async getScanStatus(): Promise<ScanStatus> {
    const response = await fetch(`${this.baseUrl}/api/library/scan`);
    if (!response.ok) {
      throw new Error(`HTTP error: ${response.status}`);
    }
    return response.json();
  }

  // ---------------------------------------------------------------------------
  // Library Management (Delete)
  // ---------------------------------------------------------------------------

  /**
   * Delete an album and all its tracks from the library.
   * @param albumId Album ID to delete
   * @returns Deletion result with counts
   */
  async deleteAlbum(albumId: string | number): Promise<{
    deleted: boolean;
    album_id: number;
    album_title: string;
    tracks_deleted: number;
    orphan_albums_deleted: number;
    orphan_artists_deleted: number;
    orphan_genres_deleted: number;
  }> {
    const response = await fetch(
      `${this.baseUrl}/api/library/albums/${albumId}`,
      { method: "DELETE" },
    );
    if (!response.ok) {
      const error = await response
        .json()
        .catch(() => ({ detail: "Unknown error" }));
      throw new Error(error.detail || `HTTP error: ${response.status}`);
    }
    return response.json();
  }

  /**
   * Delete a single track from the library.
   * @param trackId Track ID to delete
   * @returns Deletion result with counts
   */
  async deleteTrack(trackId: number): Promise<{
    deleted: boolean;
    track_id: number;
    track_title: string;
    orphan_albums_deleted: number;
    orphan_artists_deleted: number;
    orphan_genres_deleted: number;
  }> {
    const response = await fetch(
      `${this.baseUrl}/api/library/tracks/${trackId}`,
      { method: "DELETE" },
    );
    if (!response.ok) {
      const error = await response
        .json()
        .catch(() => ({ detail: "Unknown error" }));
      throw new Error(error.detail || `HTTP error: ${response.status}`);
    }
    return response.json();
  }

  // ---------------------------------------------------------------------------
  // BlurHash Placeholders (REST API)
  // ---------------------------------------------------------------------------

  /**
   * Get BlurHash placeholder for a track's artwork.
   * @param trackId Track ID
   * @returns BlurHash string or null if not available
   */
  async getTrackBlurHash(trackId: number): Promise<string | null> {
    try {
      const response = await fetch(
        `${this.baseUrl}/api/artwork/track/${trackId}/blurhash`,
      );
      if (!response.ok) {
        return null;
      }
      const data = await response.json();
      return data.blurhash || null;
    } catch {
      return null;
    }
  }

  /**
   * Get BlurHash placeholder for an album's artwork.
   * @param albumId Album ID
   * @returns BlurHash string or null if not available
   */
  async getAlbumBlurHash(albumId: number): Promise<string | null> {
    try {
      const response = await fetch(
        `${this.baseUrl}/api/artwork/album/${albumId}/blurhash`,
      );
      if (!response.ok) {
        return null;
      }
      const data = await response.json();
      return data.blurhash || null;
    } catch {
      return null;
    }
  }

  /**
   * Check if BlurHash support is available on the server.
   * @returns true if BlurHash is available
   */
  async isBlurHashAvailable(): Promise<boolean> {
    try {
      const response = await fetch(`${this.baseUrl}/api/artwork/test`);
      if (!response.ok) {
        return false;
      }
      const data = await response.json();
      return data.blurhash_available === true;
    } catch {
      return false;
    }
  }
}

// Export singleton instance
export const api = new ResonanceAPI();

// Also export class for custom instances
export { ResonanceAPI };



