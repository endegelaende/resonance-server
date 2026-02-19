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
  // Remote/radio stream metadata (LMS-compatible)
  remote?: number;
  source?: string;
  isLive?: boolean;
  currentTitle?: string;
  icyArtist?: string;
  icyTitle?: string;
  contentType?: string;
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

/** Server settings as returned by GET /api/settings */
export interface ServerSettingsData {
  // Network
  host: string;
  slimproto_port: number;
  web_port: number;
  cli_port: number;
  cors_origins: string[];
  // Library
  music_folders: string[];
  scan_on_startup: boolean;
  auto_rescan: boolean;
  // Playback defaults
  default_volume: number;
  default_repeat: number;
  default_transition_type: number;
  default_transition_duration: number;
  default_replay_gain_mode: number;
  // Paths
  data_dir: string;
  cache_dir: string;
  // Logging
  log_level: string;
  log_file: string | null;
}

/** Metadata about each setting field (runtime-changeable vs restart-required) */
export type SettingsFieldMeta = Record<string, "runtime" | "restart_required">;

/** Response from GET /api/settings */
export interface SettingsResponse {
  settings: ServerSettingsData;
  sections: Record<string, Record<string, unknown>>;
  meta: SettingsFieldMeta;
  config_file: string | null;
}

/** Response from PUT /api/settings and POST /api/settings/reset */
export interface SettingsUpdateResponse {
  settings: ServerSettingsData;
  warnings: string[];
  config_file: string | null;
}

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

// ---- Favorites ----
export interface FavoriteItem {
  id: string;
  name: string;
  url: string;
  type: string;
  icon?: string;
  hasitems: boolean;
}

export interface FavoritesResult {
  items: FavoriteItem[];
  total: number;
}

// ---- Radio ----
export interface RadioItem {
  name: string;
  url: string;
  type: string;
  icon?: string;
  hasitems: boolean;
  /** Category key for drill-down (e.g. "popular", "country:DE", "tag:jazz"). */
  category?: string;
  bitrate?: number;
  codec?: string;
  country?: string;
  countrycode?: string;
  tags?: string;
  stationuuid?: string;
  subtext?: string;
  votes?: number;
  homepage?: string;
}

export interface RadioResult {
  items: RadioItem[];
  total: number;
}

// ---- Podcasts ----
export interface PodcastItem {
  name: string;
  url: string;
  type: string;
  icon?: string;
  hasitems: boolean;
  subtitle?: string;
}

export interface PodcastResult {
  items: PodcastItem[];
  total: number;
}

// ---- Saved Playlists ----
export interface SavedPlaylist {
  id: string;
  playlist: string;
  url: string;
}

export interface SavedPlaylistTrack {
  title: string;
  url: string;
  artist?: string;
  album?: string;
  duration?: number;
  "playlist index": number;
}

// ---- Plugin Management ----
export interface PluginInfo {
  name: string;
  version: string;
  description: string;
  author: string;
  category: string;
  icon: string;
  state: "enabled" | "disabled";
  started: boolean;
  type: "core" | "community";
  has_settings: boolean;
  can_uninstall: boolean;
}

export interface PluginSettingDefinition {
  key: string;
  type: "string" | "int" | "float" | "bool" | "select";
  label: string;
  description?: string;
  default: unknown;
  secret?: boolean;
  required?: boolean;
  order: number;
  restart_required?: boolean;
  min?: number;
  max?: number;
  options?: string[];
}

export interface PluginSettingsResponse {
  plugin_name: string;
  definitions: PluginSettingDefinition[];
  values: Record<string, unknown>;
  updated?: string[];
  restart_required?: boolean;
}

export interface RepositoryPlugin {
  name: string;
  version: string;
  description: string;
  author: string;
  category: string;
  icon: string;
  min_resonance_version: string;
  url: string;
  sha256: string;
  homepage: string;
  changelog: string;
  tags: string[];
  installed_version: string | null;
  update_available: boolean;
  is_core: boolean;
  compatible: boolean;
  incompatible_reason: string;
  can_install: boolean;
  can_update: boolean;
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

    // Plugin commands (radio, podcast, favorites, …) return errors inside
    // the result body as { error: "…" } rather than as a JSON-RPC transport
    // error.  Without this check the caller receives a "successful" response
    // containing an error message, leading to misleading Success-toasts in
    // the UI while the operation actually failed.
    const res = data.result as Record<string, unknown> | null;
    if (
      res &&
      typeof res === "object" &&
      typeof (res as Record<string, unknown>).error === "string"
    ) {
      const msg = (res as Record<string, unknown>).error as string;
      console.error("[api.rpc] Result-level error:", msg);
      throw new Error(msg);
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
      // NOTE: backend sends snake_case keys for remote fields — mapped below.
      currentTrack?: Track & {
        is_live?: boolean;
        current_title?: string;
        icy_artist?: string;
        icy_title?: string;
        content_type?: string;
      };
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

    // Map snake_case remote fields from backend to camelCase Track interface
    let mappedCurrentTrack: Track | undefined;
    if (result.currentTrack) {
      const ct = result.currentTrack;
      mappedCurrentTrack = {
        ...ct,
        // Map snake_case → camelCase for remote/radio fields
        isLive: ct.is_live ?? ct.isLive,
        currentTitle: ct.current_title ?? ct.currentTitle,
        icyArtist: ct.icy_artist ?? ct.icyArtist,
        icyTitle: ct.icy_title ?? ct.icyTitle,
        contentType: ct.content_type ?? ct.contentType,
      };
    } else if (currentTrackFromLoop) {
      mappedCurrentTrack = {
        id: currentTrackFromLoop.id,
        title: currentTrackFromLoop.title,
        artist: currentTrackFromLoop.artist,
        album: currentTrackFromLoop.album,
        duration: currentTrackFromLoop.duration,
        path: currentTrackFromLoop.url || "",
        coverArt:
          currentTrackFromLoop.coverArt || currentTrackFromLoop.artwork_url,
      };
    }

    return {
      mode: result.mode || "stop",
      volume: result["mixer volume"] || 50,
      muted: result["mixer volume"] === 0,
      time: result.time || 0,
      duration: result.duration || 0,
      currentTrack: mappedCurrentTrack,
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
      typeof prefValue === "boolean"
        ? prefValue
          ? "1"
          : "0"
        : String(prefValue);
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
    const [
      transitionType,
      transitionDuration,
      transitionSmart,
      replayGainMode,
      remoteReplayGain,
      gapless,
    ] = await Promise.all([
      this.setPlayerPref(playerId, "transitionType", prefs.transitionType),
      this.setPlayerPref(
        playerId,
        "transitionDuration",
        prefs.transitionDuration,
      ),
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
      args.push(
        `volume:${Math.max(0, Math.min(100, Math.floor(update.volume)))}`,
      );
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
        time: Number.isFinite(alarm.time)
          ? Math.max(0, Math.floor(alarm.time || 0))
          : 0,
        dow: Array.from(new Set(rawDow)).sort((a, b) => a - b),
        enabled: (alarm.enabled || 0) !== 0,
        repeat: (alarm.repeat || 0) !== 0,
        volume: Number.isFinite(alarm.volume)
          ? Math.max(0, Math.floor(alarm.volume || 0))
          : 50,
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

  async setDefaultAlarmVolume(
    playerId: string,
    volume: number,
  ): Promise<number> {
    const sanitized = Math.max(0, Math.min(100, Math.floor(volume)));
    const result = await this.rpc<{ volume?: number }>(playerId, [
      "alarm",
      "defaultvolume",
      `volume:${sanitized}`,
    ]);
    return Number.isFinite(result.volume)
      ? Math.max(0, Math.floor(result.volume || 0))
      : sanitized;
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

  async removeFromPlaylist(playerId: string, index: number): Promise<void> {
    await this.rpc(playerId, ["playlist", "delete", String(index)]);
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

  // ---------------------------------------------------------------------------
  // Settings
  // ---------------------------------------------------------------------------

  /**
   * Get current server settings with metadata.
   */
  async getSettings(): Promise<SettingsResponse> {
    const response = await fetch(`${this.baseUrl}/api/settings`);
    if (!response.ok) {
      throw new Error(`Failed to get settings: ${response.status}`);
    }
    return await response.json();
  }

  /**
   * Update server settings (partial update).
   * Only provided fields are changed. Returns updated settings + warnings.
   *
   * @param updates - Object with setting field names and new values.
   *   Example: `{ default_volume: 80, log_level: "DEBUG" }`
   */
  async updateSettings(
    updates: Partial<ServerSettingsData>,
  ): Promise<SettingsUpdateResponse> {
    const response = await fetch(`${this.baseUrl}/api/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: updates }),
    });
    if (!response.ok) {
      const error = await response
        .json()
        .catch(() => ({ detail: response.statusText }));
      throw new Error(
        error.detail || `Failed to update settings: ${response.status}`,
      );
    }
    return await response.json();
  }

  /**
   * Reset all settings to their built-in defaults.
   * The config file path is preserved.
   */
  async resetSettings(): Promise<SettingsUpdateResponse> {
    const response = await fetch(`${this.baseUrl}/api/settings/reset`, {
      method: "POST",
    });
    if (!response.ok) {
      const error = await response
        .json()
        .catch(() => ({ detail: response.statusText }));
      throw new Error(
        error.detail || `Failed to reset settings: ${response.status}`,
      );
    }
    return await response.json();
  }

  // =========================================================================
  // Plugin Management (REST API)
  // =========================================================================

  async getPlugins(): Promise<{
    count: number;
    plugins: PluginInfo[];
    restart_required: boolean;
  }> {
    const response = await fetch(`${this.baseUrl}/api/plugins`);
    if (!response.ok) {
      throw new Error(`Failed to fetch plugins: ${response.status}`);
    }
    return response.json();
  }

  async enablePlugin(name: string): Promise<void> {
    const response = await fetch(`${this.baseUrl}/api/plugins/${name}/enable`, {
      method: "POST",
    });
    if (!response.ok) {
      const error = await response
        .json()
        .catch(() => ({ detail: response.statusText }));
      throw new Error(error.detail || `Failed to enable plugin: ${name}`);
    }
  }

  async disablePlugin(name: string): Promise<void> {
    const response = await fetch(
      `${this.baseUrl}/api/plugins/${name}/disable`,
      {
        method: "POST",
      },
    );
    if (!response.ok) {
      const error = await response
        .json()
        .catch(() => ({ detail: response.statusText }));
      throw new Error(error.detail || `Failed to disable plugin: ${name}`);
    }
  }

  async uninstallPlugin(name: string): Promise<void> {
    const response = await fetch(
      `${this.baseUrl}/api/plugins/${name}/uninstall`,
      {
        method: "POST",
      },
    );
    if (!response.ok) {
      const error = await response
        .json()
        .catch(() => ({ detail: response.statusText }));
      throw new Error(error.detail || `Failed to uninstall plugin: ${name}`);
    }
  }

  async getPluginSettings(name: string): Promise<PluginSettingsResponse> {
    const response = await fetch(
      `${this.baseUrl}/api/plugins/${name}/settings`,
    );
    if (!response.ok) {
      const error = await response
        .json()
        .catch(() => ({ detail: response.statusText }));
      throw new Error(error.detail || `Failed to fetch settings for: ${name}`);
    }
    return response.json();
  }

  async updatePluginSettings(
    name: string,
    values: Record<string, unknown>,
  ): Promise<PluginSettingsResponse> {
    const response = await fetch(
      `${this.baseUrl}/api/plugins/${name}/settings`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(values),
      },
    );
    if (!response.ok) {
      const error = await response
        .json()
        .catch(() => ({ detail: response.statusText }));
      throw new Error(error.detail || `Failed to save settings for: ${name}`);
    }
    return response.json();
  }

  async getRepository(forceRefresh = false): Promise<{
    count: number;
    plugins: RepositoryPlugin[];
  }> {
    const params = forceRefresh ? "?force_refresh=true" : "";
    const response = await fetch(
      `${this.baseUrl}/api/plugins/repository${params}`,
    );
    if (!response.ok) {
      throw new Error(`Failed to fetch plugin repository: ${response.status}`);
    }
    return response.json();
  }

  async installFromRepository(name: string): Promise<void> {
    const response = await fetch(
      `${this.baseUrl}/api/plugins/install-from-repo`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      },
    );
    if (!response.ok) {
      const error = await response
        .json()
        .catch(() => ({ detail: response.statusText }));
      throw new Error(error.detail || `Failed to install plugin: ${name}`);
    }
  }

  // =========================================================================
  // Favorites (Plugin: favorites)
  // =========================================================================

  /**
   * Browse favorites. Pass `itemId` to browse a sub-folder.
   */
  async getFavorites(
    start = 0,
    count = 100,
    itemId?: string,
  ): Promise<FavoritesResult> {
    const cmd: string[] = ["favorites", "items", String(start), String(count)];
    if (itemId) cmd.push(`item_id:${itemId}`);
    const result = await this.rpc<Record<string, unknown>>("-", cmd);
    const loop = (result.loop || []) as Record<string, unknown>[];
    const items: FavoriteItem[] = loop.map((r) => ({
      id: String(r.id ?? ""),
      name: String(r.name ?? r.title ?? ""),
      url: String(r.url ?? ""),
      type: String(
        (r.type ?? r.isaudio) ? "audio" : r.hasitems ? "folder" : "audio",
      ),
      icon: r.icon ? String(r.icon) : r.image ? String(r.image) : undefined,
      hasitems: r.hasitems === 1 || r.hasitems === true,
    }));
    return { items, total: Number(result.count ?? items.length) };
  }

  /**
   * Add a URL to favorites.
   */
  async addFavorite(url: string, title: string, type = "audio"): Promise<void> {
    await this.rpc("-", [
      "favorites",
      "add",
      `url:${url}`,
      `title:${title}`,
      `type:${type}`,
    ]);
  }

  /**
   * Delete a favorite by index id (e.g. "0", "1.2").
   */
  async deleteFavorite(itemId: string): Promise<void> {
    await this.rpc("-", ["favorites", "delete", `item_id:${itemId}`]);
  }

  /**
   * Rename a favorite.
   */
  async renameFavorite(itemId: string, title: string): Promise<void> {
    await this.rpc("-", [
      "favorites",
      "rename",
      `item_id:${itemId}`,
      `title:${title}`,
    ]);
  }

  /**
   * Add a folder to favorites.
   */
  async addFavoriteFolder(title: string, parentId?: string): Promise<void> {
    const cmd = ["favorites", "addlevel", `title:${title}`];
    if (parentId) cmd.push(`item_id:${parentId}`);
    await this.rpc("-", cmd);
  }

  /**
   * Check if a URL exists in favorites.
   */
  async favoriteExists(url: string): Promise<boolean> {
    const result = await this.rpc<Record<string, unknown>>("-", [
      "favorites",
      "exists",
      url,
    ]);
    return result.exists === 1 || result.exists === true;
  }

  /**
   * Play all favorites (or folder contents) on the selected player.
   */
  async playFavorites(
    playerId: string,
    method: "play" | "add" | "insert" = "play",
    itemId?: string,
  ): Promise<void> {
    const cmd = ["favorites", "playlist", method];
    if (itemId) cmd.push(`item_id:${itemId}`);
    await this.rpc(playerId, cmd);
  }

  // =========================================================================
  // Radio (Plugin: radio / radio-browser.info)
  // =========================================================================

  /**
   * Browse radio categories / stations. Pass `category` to drill into a category.
   * Categories: "popular", "trending", "country", "country:DE", "tag", "tag:jazz",
   * "language", "language:german".
   */
  async getRadioItems(
    start = 0,
    count = 100,
    category?: string,
  ): Promise<RadioResult> {
    const cmd: string[] = ["radio", "items", String(start), String(count)];
    if (category) cmd.push(`category:${category}`);
    const result = await this.rpc<Record<string, unknown>>("-", cmd);
    const loop = (result.loop || []) as Record<string, unknown>[];
    const items: RadioItem[] = loop.map((r) => ({
      name: String(r.name ?? r.title ?? ""),
      url: String(r.url ?? r.URL ?? ""),
      type: String(r.type ?? "link"),
      icon: r.icon ? String(r.icon) : r.image ? String(r.image) : undefined,
      hasitems:
        r.hasitems === 1 || r.hasitems === true || r.category !== undefined,
      category: r.category ? String(r.category) : undefined,
      bitrate: r.bitrate ? Number(r.bitrate) : undefined,
      codec: r.codec ? String(r.codec) : undefined,
      country: r.country ? String(r.country) : undefined,
      countrycode: r.countrycode ? String(r.countrycode) : undefined,
      tags: r.tags ? String(r.tags) : undefined,
      stationuuid: r.id ? String(r.id) : undefined,
      subtext: r.subtext ? String(r.subtext) : undefined,
      votes: r.votes ? Number(r.votes) : undefined,
      homepage: r.homepage ? String(r.homepage) : undefined,
    }));
    return { items, total: Number(result.count ?? items.length) };
  }

  /**
   * Search radio stations via radio-browser.info.
   */
  async searchRadio(
    query: string,
    start = 0,
    count = 100,
  ): Promise<RadioResult> {
    const cmd: string[] = [
      "radio",
      "search",
      String(start),
      String(count),
      `term:${query}`,
    ];
    const result = await this.rpc<Record<string, unknown>>("-", cmd);
    const loop = (result.loop || []) as Record<string, unknown>[];
    const items: RadioItem[] = loop.map((r) => ({
      name: String(r.name ?? r.title ?? ""),
      url: String(r.url ?? r.URL ?? ""),
      type: String(r.type ?? "audio"),
      icon: r.icon ? String(r.icon) : r.image ? String(r.image) : undefined,
      hasitems: r.hasitems === 1 || r.hasitems === true,
      bitrate: r.bitrate ? Number(r.bitrate) : undefined,
      codec: r.codec ? String(r.codec) : undefined,
      country: r.country ? String(r.country) : undefined,
      countrycode: r.countrycode ? String(r.countrycode) : undefined,
      tags: r.tags ? String(r.tags) : undefined,
      stationuuid: r.id ? String(r.id) : undefined,
      subtext: r.subtext ? String(r.subtext) : undefined,
      votes: r.votes ? Number(r.votes) : undefined,
      homepage: r.homepage ? String(r.homepage) : undefined,
    }));
    return { items, total: Number(result.count ?? items.length) };
  }

  /**
   * Play a radio station on a player.
   */
  async playRadio(
    playerId: string,
    stationUrl: string,
    title?: string,
    method: "play" | "add" | "insert" = "play",
    extra?: {
      icon?: string;
      codec?: string;
      bitrate?: number;
      stationuuid?: string;
    },
  ): Promise<void> {
    const cmd = ["radio", "play", `url:${stationUrl}`, `cmd:${method}`];
    if (title) cmd.push(`title:${title}`);
    if (extra?.icon) cmd.push(`icon:${extra.icon}`);
    if (extra?.codec) cmd.push(`codec:${extra.codec}`);
    if (extra?.bitrate != null) cmd.push(`bitrate:${extra.bitrate}`);
    if (extra?.stationuuid) cmd.push(`id:${extra.stationuuid}`);
    await this.rpc(playerId, cmd);
  }

  // =========================================================================
  // Podcasts (Plugin: podcast)
  // =========================================================================

  /**
   * Browse podcasts. Without `feedUrl`, returns top-level (subscriptions etc.).
   * With `feedUrl`, returns episodes for that feed.
   */
  async getPodcastItems(
    start = 0,
    count = 100,
    feedUrl?: string,
  ): Promise<PodcastResult> {
    const cmd: string[] = ["podcast", "items", String(start), String(count)];
    if (feedUrl) cmd.push(`url:${feedUrl}`);
    const result = await this.rpc<Record<string, unknown>>("-", cmd);
    const loop = (result.loop || []) as Record<string, unknown>[];
    const items: PodcastItem[] = loop.map((r) => ({
      name: String(r.name ?? r.text ?? r.title ?? ""),
      url: String(r.url ?? ""),
      type: String(r.type ?? "link"),
      icon: r.icon ? String(r.icon) : r.image ? String(r.image) : undefined,
      hasitems: r.hasitems === 1 || r.hasitems === true,
      subtitle: r.subtitle ? String(r.subtitle) : undefined,
    }));
    return { items, total: Number(result.count ?? items.length) };
  }

  /**
   * Search PodcastIndex for podcasts.
   */
  async searchPodcasts(
    query: string,
    start = 0,
    count = 100,
  ): Promise<PodcastResult> {
    const cmd: string[] = [
      "podcast",
      "items",
      String(start),
      String(count),
      `search:${query}`,
    ];
    const result = await this.rpc<Record<string, unknown>>("-", cmd);
    const loop = (result.loop || []) as Record<string, unknown>[];
    const items: PodcastItem[] = loop.map((r) => ({
      name: String(r.name ?? r.text ?? r.title ?? ""),
      url: String(r.url ?? ""),
      type: String(r.type ?? "folder"),
      icon: r.icon ? String(r.icon) : r.image ? String(r.image) : undefined,
      hasitems: r.hasitems === 1 || r.hasitems === true,
      subtitle: r.subtitle
        ? String(r.subtitle)
        : r.description
          ? String(r.description)
          : undefined,
    }));
    return { items, total: Number(result.count ?? items.length) };
  }

  /**
   * Play a podcast episode on a player.
   */
  async playPodcast(
    playerId: string,
    episodeUrl: string,
    title?: string,
    method: "play" | "add" | "insert" = "play",
  ): Promise<void> {
    const cmd = ["podcast", "play", `url:${episodeUrl}`, `cmd:${method}`];
    if (title) cmd.push(`title:${title}`);
    await this.rpc(playerId, cmd);
  }

  /**
   * Subscribe to a podcast feed.
   */
  async podcastSubscribe(feedUrl: string, title?: string): Promise<void> {
    const cmd = ["podcast", "addshow", `url:${feedUrl}`];
    if (title) cmd.push(`name:${title}`);
    await this.rpc("-", cmd);
  }

  /**
   * Unsubscribe from a podcast feed.
   */
  async podcastUnsubscribe(feedUrl: string): Promise<void> {
    await this.rpc("-", ["podcast", "delshow", `url:${feedUrl}`]);
  }

  // =========================================================================
  // Saved Playlists (LMS-compat: playlists command)
  // =========================================================================

  /**
   * List saved playlists on disk (M3U files).
   */
  async getSavedPlaylists(
    start = 0,
    count = 200,
    search?: string,
  ): Promise<{ playlists: SavedPlaylist[]; total: number }> {
    const cmd: string[] = ["playlists", String(start), String(count)];
    if (search) cmd.push(`search:${search}`);
    const result = await this.rpc<Record<string, unknown>>("-", cmd);
    const loop = (result.playlists_loop || []) as Record<string, unknown>[];
    const playlists: SavedPlaylist[] = loop.map((r) => ({
      id: String(r.id ?? ""),
      playlist: String(r.playlist ?? ""),
      url: String(r.url ?? ""),
    }));
    return { playlists, total: Number(result.count ?? playlists.length) };
  }

  /**
   * Get tracks from a saved playlist.
   */
  async getSavedPlaylistTracks(
    playlistId: string,
    start = 0,
    count = 200,
  ): Promise<{ tracks: SavedPlaylistTrack[]; total: number }> {
    const result = await this.rpc<Record<string, unknown>>("-", [
      "playlists",
      "tracks",
      `playlist_id:${playlistId}`,
      `_index:${start}`,
      `_quantity:${count}`,
    ]);
    const loop = (result.playlisttracks_loop || []) as Record<
      string,
      unknown
    >[];
    const tracks: SavedPlaylistTrack[] = loop.map((r) => ({
      title: String(r.title ?? ""),
      url: String(r.url ?? ""),
      artist: r.artist ? String(r.artist) : undefined,
      album: r.album ? String(r.album) : undefined,
      duration: r.duration ? Number(r.duration) : undefined,
      "playlist index": Number(r["playlist index"] ?? 0),
    }));
    return { tracks, total: Number(result.count ?? tracks.length) };
  }

  /**
   * Save the current player queue as a named playlist (M3U).
   */
  async savePlaylist(playerId: string, name: string): Promise<void> {
    await this.rpc(playerId, ["playlist", "save", name]);
  }

  /**
   * Load a saved playlist into the player queue and start playing.
   */
  async loadSavedPlaylist(playerId: string, name: string): Promise<void> {
    await this.rpc(playerId, ["playlist", "resume", name]);
  }

  /**
   * Delete a saved playlist.
   */
  async deleteSavedPlaylist(playlistId: string): Promise<void> {
    await this.rpc("-", ["playlists", "delete", `playlist_id:${playlistId}`]);
  }

  /**
   * Rename a saved playlist.
   */
  async renameSavedPlaylist(
    playlistId: string,
    newName: string,
  ): Promise<void> {
    await this.rpc("-", [
      "playlists",
      "rename",
      `playlist_id:${playlistId}`,
      `newname:${newName}`,
    ]);
  }
}

// Export singleton instance
export const api = new ResonanceAPI();

// Also export class for custom instances
export { ResonanceAPI };
