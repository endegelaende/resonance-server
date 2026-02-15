<script lang="ts">
	import { api, type ServerSettingsData, type SettingsFieldMeta, type ScanStatus } from '$lib/api';
	import { toastStore } from '$lib/stores/toast.svelte';
	import {
		Server,
		Music,
		Volume2,
		FolderPlus,
		Trash2,
		Save,
		RotateCcw,
		Loader2,
		AlertTriangle,
		Info,
		RefreshCw,
		Check,
		Shield,
	} from 'lucide-svelte';

	// ---------------------------------------------------------------------------
	// State
	// ---------------------------------------------------------------------------

	let isLoading = $state(true);
	let isSaving = $state(false);
	let isResetting = $state(false);
	let isScanning = $state(false);

	// Loaded settings
	let settings = $state<ServerSettingsData | null>(null);
	let meta = $state<SettingsFieldMeta>({});
	let configFile = $state<string | null>(null);

	// Dirty tracking: we keep a snapshot of the original values
	let originalJson = $state('');
	let isDirty = $derived(settings !== null && JSON.stringify(settings) !== originalJson);

	// Music folder input
	let newFolderPath = $state('');

	// Scan status
	let scanStatus = $state<ScanStatus | null>(null);
	let scanPollInterval: ReturnType<typeof setInterval> | null = null;

	// ---------------------------------------------------------------------------
	// Lifecycle
	// ---------------------------------------------------------------------------

	$effect(() => {
		loadSettings();
		return () => {
			if (scanPollInterval) {
				clearInterval(scanPollInterval);
				scanPollInterval = null;
			}
		};
	});

	// ---------------------------------------------------------------------------
	// Data Loading
	// ---------------------------------------------------------------------------

	async function loadSettings() {
		isLoading = true;
		try {
			const resp = await api.getSettings();
			settings = resp.settings;
			meta = resp.meta;
			configFile = resp.config_file;
			originalJson = JSON.stringify(resp.settings);
		} catch (e) {
			toastStore.error('Failed to load settings', {
				detail: e instanceof Error ? e.message : String(e),
			});
		} finally {
			isLoading = false;
		}
		// Also check scan status
		checkScanStatus();
	}

	async function checkScanStatus() {
		try {
			scanStatus = await api.getScanStatus();
			isScanning = scanStatus.scanning;
			if (isScanning && !scanPollInterval) {
				scanPollInterval = setInterval(async () => {
					try {
						scanStatus = await api.getScanStatus();
						isScanning = scanStatus.scanning;
						if (!isScanning && scanPollInterval) {
							clearInterval(scanPollInterval);
							scanPollInterval = null;
							toastStore.success(`Scan complete! Found ${scanStatus.tracks_found} tracks.`);
						}
					} catch {
						/* ignore polling errors */
					}
				}, 1000);
			}
		} catch {
			/* ignore */
		}
	}

	// ---------------------------------------------------------------------------
	// Actions
	// ---------------------------------------------------------------------------

	async function handleSave() {
		if (!settings || !isDirty) return;
		isSaving = true;
		try {
			const resp = await api.updateSettings(settings);
			settings = resp.settings;
			originalJson = JSON.stringify(resp.settings);
			configFile = resp.config_file;

			// Show warnings if any
			const restartWarnings = resp.warnings.filter((w) => w.toLowerCase().includes('restart'));
			const otherWarnings = resp.warnings.filter((w) => !w.toLowerCase().includes('restart'));

			if (restartWarnings.length > 0) {
				toastStore.warning('Settings saved', {
					detail: 'Some changes require a server restart to take effect.',
				});
			} else {
				toastStore.success('Settings saved');
			}
			for (const w of otherWarnings) {
				toastStore.info(w);
			}
		} catch (e) {
			toastStore.error('Failed to save settings', {
				detail: e instanceof Error ? e.message : String(e),
			});
		} finally {
			isSaving = false;
		}
	}

	async function handleReset() {
		isResetting = true;
		try {
			const resp = await api.resetSettings();
			settings = resp.settings;
			originalJson = JSON.stringify(resp.settings);
			configFile = resp.config_file;
			toastStore.info('All settings reset to defaults', {
				detail: 'A server restart is recommended.',
			});
		} catch (e) {
			toastStore.error('Failed to reset settings', {
				detail: e instanceof Error ? e.message : String(e),
			});
		} finally {
			isResetting = false;
		}
	}

	function handleAddFolder() {
		if (!settings || !newFolderPath.trim()) return;
		const path = newFolderPath.trim();
		if (settings.music_folders.includes(path)) {
			toastStore.warning('Folder already added');
			return;
		}
		settings.music_folders = [...settings.music_folders, path];
		newFolderPath = '';
	}

	function handleRemoveFolder(path: string) {
		if (!settings) return;
		settings.music_folders = settings.music_folders.filter((f) => f !== path);
	}

	async function handleStartScan() {
		if (!settings || settings.music_folders.length === 0) {
			toastStore.warning('Add at least one music folder before scanning');
			return;
		}
		try {
			const result = await api.startScan();
			isScanning = result.scanning;
			toastStore.info(result.status === 'started' ? 'Library scan started...' : 'Scan already running');
			checkScanStatus();
		} catch (e) {
			toastStore.error('Failed to start scan', {
				detail: e instanceof Error ? e.message : String(e),
			});
		}
	}

	function handleFolderKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter') {
			handleAddFolder();
		}
	}

	// ---------------------------------------------------------------------------
	// Helpers
	// ---------------------------------------------------------------------------

	function fieldBadge(field: string): 'runtime' | 'restart_required' | null {
		return meta[field] ?? null;
	}

	const transitionTypeLabels: Record<number, string> = {
		0: 'None',
		1: 'Crossfade',
		2: 'Fade In',
		3: 'Fade Out',
		4: 'Fade In & Out',
	};

	const repeatLabels: Record<number, string> = {
		0: 'Off',
		1: 'Song',
		2: 'Playlist',
	};

	const replayGainLabels: Record<number, string> = {
		0: 'Off',
		1: 'Track',
		2: 'Album',
		3: 'Smart',
	};

	const logLevelOptions = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];
</script>

<div class="p-6 space-y-6 max-w-3xl mx-auto">
	<!-- Header -->
	<div class="flex items-center justify-between">
		<div>
			<h2 class="text-2xl font-semibold text-text">Settings</h2>
			<p class="text-sm text-overlay-1 mt-1">
				Server configuration and playback defaults
			</p>
		</div>
		{#if isDirty}
			<div class="flex items-center gap-2">
				<span class="text-xs text-warning font-medium flex items-center gap-1">
					<AlertTriangle size={14} />
					Unsaved changes
				</span>
			</div>
		{/if}
	</div>

	{#if isLoading}
		<!-- Loading spinner -->
		<div class="flex items-center justify-center py-16">
			<Loader2 size={32} class="animate-spin text-accent" />
		</div>
	{:else if settings}
		<!-- ================================================================= -->
		<!-- Server Info (read-only)                                            -->
		<!-- ================================================================= -->
		<section class="rounded-xl border border-border bg-surface-0 overflow-hidden">
			<button
				type="button"
				class="w-full flex items-center gap-3 px-5 py-4 text-left"
				disabled
			>
				<div class="w-9 h-9 rounded-lg bg-accent/10 flex items-center justify-center shrink-0">
					<Server size={18} class="text-accent" />
				</div>
				<div class="flex-1">
					<h3 class="text-sm font-semibold text-text">Server Info</h3>
					<p class="text-xs text-overlay-1">Network configuration and paths</p>
				</div>
				<span class="text-[10px] text-overlay-0 bg-surface-1 px-2 py-0.5 rounded-full">read-only</span>
			</button>
			<div class="px-5 pb-5 space-y-3 border-t border-border pt-4">
				<div class="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
					<div>
						<span class="text-overlay-1">Host</span>
						<p class="text-text font-mono">{settings.host}</p>
					</div>
					<div>
						<span class="text-overlay-1">Web Port</span>
						<p class="text-text font-mono">{settings.web_port}</p>
					</div>
					<div>
						<span class="text-overlay-1">Slimproto Port</span>
						<p class="text-text font-mono">{settings.slimproto_port}</p>
					</div>
					<div>
						<span class="text-overlay-1">CLI Port</span>
						<p class="text-text font-mono">
							{settings.cli_port === 0 ? 'Disabled' : settings.cli_port}
						</p>
					</div>
					<div>
						<span class="text-overlay-1">CORS Origins</span>
						<p class="text-text font-mono text-xs">
							{settings.cors_origins.join(', ')}
						</p>
					</div>
					<div>
						<span class="text-overlay-1">Data Directory</span>
						<p class="text-text font-mono text-xs truncate" title={settings.data_dir}>
							{settings.data_dir}
						</p>
					</div>
					<div>
						<span class="text-overlay-1">Cache Directory</span>
						<p class="text-text font-mono text-xs truncate" title={settings.cache_dir}>
							{settings.cache_dir}
						</p>
					</div>
					{#if configFile}
						<div>
							<span class="text-overlay-1">Config File</span>
							<p class="text-text font-mono text-xs truncate" title={configFile}>
								{configFile}
							</p>
						</div>
					{/if}
				</div>
				<p class="text-xs text-overlay-0 flex items-center gap-1 pt-1">
					<Info size={12} />
					Network and path settings require a server restart to take effect.
				</p>
			</div>
		</section>

		<!-- ================================================================= -->
		<!-- Music Folders                                                      -->
		<!-- ================================================================= -->
		<section class="rounded-xl border border-border bg-surface-0 overflow-hidden">
			<div class="flex items-center gap-3 px-5 py-4">
				<div class="w-9 h-9 rounded-lg bg-green-500/10 flex items-center justify-center shrink-0">
					<Music size={18} class="text-green-400" />
				</div>
				<div class="flex-1">
					<h3 class="text-sm font-semibold text-text">Music Folders</h3>
					<p class="text-xs text-overlay-1">Directories to scan for music files</p>
				</div>
				{#if settings.music_folders.length > 0 && !isScanning}
					<button
						class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
							   bg-accent/10 text-accent hover:bg-accent/20 transition-colors"
						onclick={handleStartScan}
					>
						<RefreshCw size={13} />
						Rescan
					</button>
				{/if}
			</div>
			<div class="px-5 pb-5 space-y-3 border-t border-border pt-4">
				<!-- Add folder input -->
				<div class="flex gap-2">
					<input
						type="text"
						bind:value={newFolderPath}
						placeholder="Enter folder path (e.g. D:\Music or /mnt/music)"
						class="flex-1 px-3 py-2 rounded-lg bg-mantle border border-surface-1
							   text-text text-sm placeholder-overlay-0 focus:outline-none
							   focus:ring-2 focus:ring-accent/50 focus:border-accent transition-all"
						onkeydown={handleFolderKeydown}
					/>
					<button
						class="px-3 py-2 rounded-lg bg-accent hover:bg-accent-hover text-crust
							   text-sm font-medium transition-colors disabled:opacity-50
							   disabled:cursor-not-allowed flex items-center gap-1.5"
						onclick={handleAddFolder}
						disabled={!newFolderPath.trim()}
					>
						<FolderPlus size={15} />
						Add
					</button>
				</div>

				<!-- Folder list -->
				{#if settings.music_folders.length === 0}
					<div class="px-4 py-6 rounded-lg bg-mantle text-center text-overlay-1 text-sm">
						<p>No folders configured</p>
						<p class="text-xs mt-1">Add a folder above to start building your library</p>
					</div>
				{:else}
					<div class="space-y-1.5 max-h-48 overflow-y-auto">
						{#each settings.music_folders as folder}
							<div
								class="flex items-center justify-between px-3 py-2.5 rounded-lg bg-mantle group"
							>
								<span class="text-text text-sm truncate flex-1 font-mono" title={folder}>
									{folder}
								</span>
								<button
									class="p-1.5 rounded-md opacity-0 group-hover:opacity-100
										   hover:bg-error/10 text-overlay-1 hover:text-error transition-all"
									onclick={() => handleRemoveFolder(folder)}
									aria-label="Remove folder"
								>
									<Trash2 size={14} />
								</button>
							</div>
						{/each}
					</div>
				{/if}

				<!-- Scan status -->
				{#if isScanning && scanStatus}
					<div class="px-4 py-3 rounded-lg bg-mantle space-y-2">
						<div class="flex items-center gap-2 text-accent text-sm">
							<Loader2 size={14} class="animate-spin" />
							<span class="font-medium">Scanning...</span>
						</div>
						<div class="w-full h-1.5 bg-surface-1 rounded-full overflow-hidden">
							<div
								class="h-full bg-accent transition-all duration-300"
								style="width: {scanStatus.progress * 100}%"
							></div>
						</div>
						<div class="flex justify-between text-xs text-overlay-1">
							<span>{scanStatus.tracks_found} tracks found</span>
							<span>{Math.round(scanStatus.progress * 100)}%</span>
						</div>
					</div>
				{/if}

				<!-- Scan options -->
				<div class="flex items-center gap-6 pt-1">
					<label class="flex items-center gap-2 text-sm text-overlay-1 cursor-pointer">
						<input
							type="checkbox"
							bind:checked={settings.scan_on_startup}
							class="w-4 h-4 rounded border-surface-1 bg-mantle text-accent
								   focus:ring-accent/50 focus:ring-2 cursor-pointer"
						/>
						Scan on startup
					</label>
					<label class="flex items-center gap-2 text-sm text-overlay-1 cursor-pointer">
						<input
							type="checkbox"
							bind:checked={settings.auto_rescan}
							class="w-4 h-4 rounded border-surface-1 bg-mantle text-accent
								   focus:ring-accent/50 focus:ring-2 cursor-pointer"
						/>
						Auto rescan
					</label>
				</div>
			</div>
		</section>

		<!-- ================================================================= -->
		<!-- Playback Defaults                                                 -->
		<!-- ================================================================= -->
		<section class="rounded-xl border border-border bg-surface-0 overflow-hidden">
			<div class="flex items-center gap-3 px-5 py-4">
				<div class="w-9 h-9 rounded-lg bg-purple-500/10 flex items-center justify-center shrink-0">
					<Volume2 size={18} class="text-purple-400" />
				</div>
				<div class="flex-1">
					<h3 class="text-sm font-semibold text-text">Playback Defaults</h3>
					<p class="text-xs text-overlay-1">Default settings for new player connections</p>
				</div>
			</div>
			<div class="px-5 pb-5 space-y-5 border-t border-border pt-4">
				<!-- Default Volume -->
				<div class="space-y-2">
					<div class="flex items-center justify-between">
						<label for="default-volume" class="text-sm text-overlay-1">Default Volume</label>
						<span class="text-sm text-text font-mono tabular-nums w-10 text-right">
							{settings.default_volume}%
						</span>
					</div>
					<input
						id="default-volume"
						type="range"
						min="0"
						max="100"
						step="1"
						bind:value={settings.default_volume}
						class="w-full h-1.5 bg-surface-1 rounded-full appearance-none cursor-pointer
							   accent-accent [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:h-4
							   [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-accent
							   [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:cursor-pointer"
					/>
				</div>

				<!-- Repeat Mode -->
				<div class="flex items-center justify-between">
					<label for="default-repeat" class="text-sm text-overlay-1">Default Repeat</label>
					<select
						id="default-repeat"
						bind:value={settings.default_repeat}
						class="px-3 py-1.5 rounded-lg bg-mantle border border-surface-1
							   text-text text-sm focus:outline-none focus:ring-2
							   focus:ring-accent/50 cursor-pointer"
					>
						{#each Object.entries(repeatLabels) as [value, label]}
							<option value={Number(value)}>{label}</option>
						{/each}
					</select>
				</div>

				<!-- Transition Type -->
				<div class="flex items-center justify-between">
					<label for="transition-type" class="text-sm text-overlay-1">Transition Type</label>
					<select
						id="transition-type"
						bind:value={settings.default_transition_type}
						class="px-3 py-1.5 rounded-lg bg-mantle border border-surface-1
							   text-text text-sm focus:outline-none focus:ring-2
							   focus:ring-accent/50 cursor-pointer"
					>
						{#each Object.entries(transitionTypeLabels) as [value, label]}
							<option value={Number(value)}>{label}</option>
						{/each}
					</select>
				</div>

				<!-- Transition Duration -->
				{#if settings.default_transition_type > 0}
					<div class="space-y-2">
						<div class="flex items-center justify-between">
							<label for="transition-duration" class="text-sm text-overlay-1">
								Transition Duration
							</label>
							<span class="text-sm text-text font-mono tabular-nums">
								{settings.default_transition_duration}s
							</span>
						</div>
						<input
							id="transition-duration"
							type="range"
							min="1"
							max="30"
							step="1"
							bind:value={settings.default_transition_duration}
							class="w-full h-1.5 bg-surface-1 rounded-full appearance-none cursor-pointer
								   accent-accent [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:h-4
								   [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-accent
								   [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:cursor-pointer"
						/>
					</div>
				{/if}

				<!-- Replay Gain -->
				<div class="flex items-center justify-between">
					<label for="replay-gain" class="text-sm text-overlay-1">ReplayGain Mode</label>
					<select
						id="replay-gain"
						bind:value={settings.default_replay_gain_mode}
						class="px-3 py-1.5 rounded-lg bg-mantle border border-surface-1
							   text-text text-sm focus:outline-none focus:ring-2
							   focus:ring-accent/50 cursor-pointer"
					>
						{#each Object.entries(replayGainLabels) as [value, label]}
							<option value={Number(value)}>{label}</option>
						{/each}
					</select>
				</div>
			</div>
		</section>

		<!-- ================================================================= -->
		<!-- Logging                                                            -->
		<!-- ================================================================= -->
		<section class="rounded-xl border border-border bg-surface-0 overflow-hidden">
			<div class="flex items-center gap-3 px-5 py-4">
				<div class="w-9 h-9 rounded-lg bg-yellow-500/10 flex items-center justify-center shrink-0">
					<Shield size={18} class="text-yellow-400" />
				</div>
				<div class="flex-1">
					<h3 class="text-sm font-semibold text-text">Logging</h3>
					<p class="text-xs text-overlay-1">Diagnostic and log level settings</p>
				</div>
			</div>
			<div class="px-5 pb-5 space-y-4 border-t border-border pt-4">
				<div class="flex items-center justify-between">
					<label for="log-level" class="text-sm text-overlay-1">Log Level</label>
					<select
						id="log-level"
						bind:value={settings.log_level}
						class="px-3 py-1.5 rounded-lg bg-mantle border border-surface-1
							   text-text text-sm focus:outline-none focus:ring-2
							   focus:ring-accent/50 cursor-pointer"
					>
						{#each logLevelOptions as level}
							<option value={level}>{level}</option>
						{/each}
					</select>
				</div>
				{#if settings.log_file}
					<div class="text-sm">
						<span class="text-overlay-1">Log File</span>
						<p class="text-text font-mono text-xs mt-0.5 truncate" title={settings.log_file}>
							{settings.log_file}
						</p>
					</div>
				{/if}
			</div>
		</section>

		<!-- ================================================================= -->
		<!-- Action Buttons                                                     -->
		<!-- ================================================================= -->
		<div class="flex items-center justify-between pt-2 pb-4">
			<!-- Reset -->
			<button
				class="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium
					   text-overlay-1 hover:text-error hover:bg-error/10 transition-colors"
				onclick={handleReset}
				disabled={isResetting}
			>
				{#if isResetting}
					<Loader2 size={16} class="animate-spin" />
				{:else}
					<RotateCcw size={16} />
				{/if}
				Reset to Defaults
			</button>

			<!-- Save -->
			<button
				class="flex items-center gap-2 px-6 py-2.5 rounded-xl text-sm font-medium
					   transition-all disabled:opacity-50 disabled:cursor-not-allowed
					   {isDirty
						? 'bg-accent hover:bg-accent-hover text-crust shadow-lg shadow-accent/20'
						: 'bg-surface-1 text-overlay-1'}"
				onclick={handleSave}
				disabled={!isDirty || isSaving}
			>
				{#if isSaving}
					<Loader2 size={16} class="animate-spin" />
					Saving...
				{:else if !isDirty}
					<Check size={16} />
					Saved
				{:else}
					<Save size={16} />
					Save Settings
				{/if}
			</button>
		</div>

		<!-- About -->
		<section class="rounded-xl border border-border bg-surface-0 px-5 py-4">
			<div class="flex items-center gap-3">
				<div class="w-9 h-9 rounded-lg bg-surface-1 flex items-center justify-center shrink-0">
					<Info size={18} class="text-overlay-1" />
				</div>
				<div>
					<h3 class="text-sm font-semibold text-text">About Resonance</h3>
					<p class="text-xs text-overlay-1 mt-0.5">
						Modern Python music server — LMS-compatible Squeezebox controller
					</p>
				</div>
			</div>
		</section>
	{:else}
		<!-- Error state -->
		<div class="rounded-xl border border-error/30 bg-error/5 p-6 text-center">
			<AlertTriangle size={32} class="text-error mx-auto mb-2" />
			<p class="text-text font-medium">Failed to load settings</p>
			<button
				class="mt-3 px-4 py-2 rounded-lg bg-accent text-crust text-sm font-medium hover:bg-accent-hover transition-colors"
				onclick={loadSettings}
			>
				Retry
			</button>
		</div>
	{/if}
</div>
