<script lang="ts">
	import { playerStore } from '$lib/stores/player.svelte';
	import { toastStore } from '$lib/stores/toast.svelte';
	import { api, type SavedPlaylist, type SavedPlaylistTrack } from '$lib/api';
	import {
		ListMusic,
		Play,
		Trash2,
		Pencil,
		Save,
		ChevronRight,
		ArrowLeft,
		Loader2,
		Music,
		Clock,
		X,
		Check,
		FolderOpen,
		Plus,
	} from 'lucide-svelte';

	// ---------------------------------------------------------------------------
	// State
	// ---------------------------------------------------------------------------

	let playlists = $state<SavedPlaylist[]>([]);
	let isLoading = $state(false);
	let total = $state(0);

	// Detail view state (viewing tracks of a single playlist)
	let selectedPlaylist = $state<SavedPlaylist | null>(null);
	let playlistTracks = $state<SavedPlaylistTrack[]>([]);
	let tracksTotal = $state(0);
	let isLoadingTracks = $state(false);

	// Save dialog state
	let showSaveDialog = $state(false);
	let savePlaylistName = $state('');
	let saveInput = $state<HTMLInputElement | null>(null);
	let isSaving = $state(false);

	// Rename state
	let renamingId = $state<string | null>(null);
	let renameValue = $state('');
	let renameInput = $state<HTMLInputElement | null>(null);

	// Delete confirmation state
	let deletingPlaylist = $state<SavedPlaylist | null>(null);
	let isDeleting = $state(false);

	// In-flight guard for play/load actions
	let isActionInFlight = $state(false);

	// ---------------------------------------------------------------------------
	// Data loading
	// ---------------------------------------------------------------------------

	async function loadPlaylists() {
		isLoading = true;
		try {
			const result = await api.getSavedPlaylists(0, 500);
			playlists = result.playlists;
			total = result.total;
		} catch (err) {
			console.error('Failed to load playlists:', err);
			toastStore.error('Failed to load playlists', { detail: (err as Error).message });
		} finally {
			isLoading = false;
		}
	}

	async function loadPlaylistTracks(playlist: SavedPlaylist) {
		isLoadingTracks = true;
		try {
			const result = await api.getSavedPlaylistTracks(playlist.id, 0, 1000);
			playlistTracks = result.tracks;
			tracksTotal = result.total;
		} catch (err) {
			console.error('Failed to load playlist tracks:', err);
			toastStore.error('Failed to load playlist tracks', { detail: (err as Error).message });
		} finally {
			isLoadingTracks = false;
		}
	}

	// Initial load
	$effect(() => {
		if (!selectedPlaylist) {
			loadPlaylists();
		}
	});

	// ---------------------------------------------------------------------------
	// Navigation
	// ---------------------------------------------------------------------------

	function viewPlaylist(playlist: SavedPlaylist) {
		selectedPlaylist = playlist;
		loadPlaylistTracks(playlist);
	}

	function goBack() {
		selectedPlaylist = null;
		playlistTracks = [];
		tracksTotal = 0;
	}

	// ---------------------------------------------------------------------------
	// Actions: Save
	// ---------------------------------------------------------------------------

	function openSaveDialog() {
		showSaveDialog = true;
		savePlaylistName = '';
		setTimeout(() => saveInput?.focus(), 50);
	}

	async function confirmSave() {
		const name = savePlaylistName.trim();
		if (!name) {
			showSaveDialog = false;
			return;
		}
		if (!playerStore.selectedPlayerId) {
			toastStore.warning('No player selected');
			return;
		}
		if (playerStore.playlist.length === 0) {
			toastStore.warning('Queue is empty — nothing to save');
			return;
		}

		isSaving = true;
		try {
			await api.savePlaylist(playerStore.selectedPlayerId, name);
			toastStore.success(`Playlist "${name}" saved (${playerStore.playlist.length} tracks)`);
			showSaveDialog = false;
			savePlaylistName = '';
			await loadPlaylists();
		} catch (err) {
			toastStore.error(`Failed to save playlist`, { detail: (err as Error).message });
		} finally {
			isSaving = false;
		}
	}

	function cancelSave() {
		showSaveDialog = false;
		savePlaylistName = '';
	}

	function handleSaveKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter') {
			confirmSave();
		} else if (event.key === 'Escape') {
			cancelSave();
		}
	}

	// ---------------------------------------------------------------------------
	// Actions: Load / Play
	// ---------------------------------------------------------------------------

	async function handleLoadPlaylist(playlist: SavedPlaylist) {
		if (isActionInFlight) return;
		if (!playerStore.selectedPlayerId) {
			toastStore.warning('No player selected');
			return;
		}

		isActionInFlight = true;
		try {
			await api.loadSavedPlaylist(playerStore.selectedPlayerId, playlist.id);
			toastStore.success(`Loaded "${playlist.playlist}"`);
			await playerStore.loadStatus();
			await playerStore.loadPlaylist();
		} catch (err) {
			toastStore.error(`Failed to load playlist`, { detail: (err as Error).message });
		} finally {
			isActionInFlight = false;
		}
	}

	// ---------------------------------------------------------------------------
	// Actions: Rename
	// ---------------------------------------------------------------------------

	function startRename(playlist: SavedPlaylist, event: MouseEvent) {
		event.stopPropagation();
		renamingId = playlist.id;
		renameValue = playlist.playlist;
		setTimeout(() => renameInput?.focus(), 50);
	}

	async function confirmRename() {
		if (!renamingId || !renameValue.trim()) {
			renamingId = null;
			return;
		}
		try {
			await api.renameSavedPlaylist(renamingId, renameValue.trim());
			toastStore.success('Playlist renamed');
			renamingId = null;
			renameValue = '';
			await loadPlaylists();
			// If we renamed the currently viewed playlist, update the reference
			if (selectedPlaylist && selectedPlaylist.id === renamingId) {
				selectedPlaylist = { ...selectedPlaylist, playlist: renameValue.trim() };
			}
		} catch (err) {
			toastStore.error('Failed to rename playlist', { detail: (err as Error).message });
		}
	}

	function cancelRename() {
		renamingId = null;
		renameValue = '';
	}

	function handleRenameKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter') {
			confirmRename();
		} else if (event.key === 'Escape') {
			cancelRename();
		}
	}

	// ---------------------------------------------------------------------------
	// Actions: Delete
	// ---------------------------------------------------------------------------

	function startDelete(playlist: SavedPlaylist, event: MouseEvent) {
		event.stopPropagation();
		deletingPlaylist = playlist;
	}

	async function confirmDelete() {
		if (!deletingPlaylist) return;
		const name = deletingPlaylist.playlist;
		const id = deletingPlaylist.id;

		isDeleting = true;
		try {
			await api.deleteSavedPlaylist(id);
			toastStore.success(`Deleted "${name}"`);
			// If we were viewing this playlist, go back to list
			if (selectedPlaylist && selectedPlaylist.id === id) {
				goBack();
			}
			deletingPlaylist = null;
			await loadPlaylists();
		} catch (err) {
			toastStore.error(`Failed to delete "${name}"`, { detail: (err as Error).message });
		} finally {
			isDeleting = false;
		}
	}

	function cancelDelete() {
		deletingPlaylist = null;
	}

	// ---------------------------------------------------------------------------
	// Helpers
	// ---------------------------------------------------------------------------

	function formatDuration(seconds: number | undefined): string {
		if (!seconds || seconds < 0) return '--:--';
		const mins = Math.floor(seconds / 60);
		const secs = Math.floor(seconds % 60);
		return `${mins}:${secs.toString().padStart(2, '0')}`;
	}

	function getTotalDuration(tracks: SavedPlaylistTrack[]): string {
		const total = tracks.reduce((acc, t) => acc + (t.duration || 0), 0);
		const hours = Math.floor(total / 3600);
		const mins = Math.floor((total % 3600) / 60);
		if (hours > 0) {
			return `${hours}h ${mins}m`;
		}
		return `${mins} min`;
	}
</script>

<div class="flex flex-col h-full">
	<!-- Header -->
	<div
		class="flex items-center justify-between px-6 py-4 border-b border-border bg-base/50 backdrop-blur-sm"
	>
		<div class="flex items-center gap-3 min-w-0">
			{#if selectedPlaylist}
				<button
					class="p-2 rounded-lg hover:bg-surface-0 text-overlay-1 hover:text-text transition-colors shrink-0"
					onclick={goBack}
					aria-label="Go back"
				>
					<ArrowLeft size={20} />
				</button>
				<div class="flex items-center gap-2 min-w-0">
					<ListMusic size={18} class="text-accent shrink-0" />
					<h2 class="text-text font-medium truncate">{selectedPlaylist.playlist}</h2>
				</div>
			{:else}
				<div class="flex items-center gap-2">
					<ListMusic size={18} class="text-accent" />
					<h2 class="text-text font-medium">Playlists</h2>
					{#if total > 0}
						<span class="text-sm text-overlay-1">({total})</span>
					{/if}
				</div>
			{/if}
		</div>

		<!-- Actions -->
		<div class="flex items-center gap-2 shrink-0">
			{#if selectedPlaylist}
				<!-- Load / Play button in detail view -->
				<button
					class="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent text-crust font-medium
							   hover:bg-accent-hover hover:scale-105 active:scale-95 transition-all shadow-md
							   disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100"
					onclick={() => selectedPlaylist && handleLoadPlaylist(selectedPlaylist)}
					disabled={isActionInFlight || playlistTracks.length === 0}
					aria-label="Load playlist"
				>
					<Play size={18} fill="currentColor" />
					<span class="text-sm">Load & Play</span>
				</button>
			{:else}
				<!-- Save Current Queue button -->
				<button
					class="flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-surface-0 text-overlay-1 hover:text-text transition-colors"
					onclick={openSaveDialog}
					aria-label="Save current queue"
					title="Save current queue as playlist"
				>
					<Save size={18} />
					<span class="text-sm hidden sm:inline">Save Queue</span>
				</button>
			{/if}
		</div>
	</div>

	<!-- Save Dialog (inline) -->
	{#if showSaveDialog}
		<div
			class="flex items-center gap-3 px-6 py-3 bg-surface-0/50 border-b border-border"
		>
			<Save size={18} class="text-accent shrink-0" />
			<div class="flex-1 flex flex-col gap-1">
				<input
					bind:this={saveInput}
					type="text"
					bind:value={savePlaylistName}
					onkeydown={handleSaveKeydown}
					placeholder="Playlist name…"
					class="w-full px-3 py-1.5 bg-surface-0 border border-border rounded-lg text-text
						   placeholder:text-overlay-0 focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent text-sm"
				/>
				<p class="text-xs text-overlay-1">
					Saves the current queue ({playerStore.playlist.length} tracks) as an M3U playlist
				</p>
			</div>
			<button
				class="p-1.5 rounded-lg hover:bg-surface-1 text-success transition-colors disabled:opacity-50"
				onclick={confirmSave}
				disabled={isSaving || !savePlaylistName.trim()}
				aria-label="Save"
			>
				{#if isSaving}
					<Loader2 size={18} class="animate-spin" />
				{:else}
					<Check size={18} />
				{/if}
			</button>
			<button
				class="p-1.5 rounded-lg hover:bg-surface-1 text-overlay-1 transition-colors"
				onclick={cancelSave}
				aria-label="Cancel"
			>
				<X size={18} />
			</button>
		</div>
	{/if}

	<!-- Delete Confirmation -->
	{#if deletingPlaylist}
		<div
			class="flex items-center gap-3 px-6 py-3 bg-error/10 border-b border-error/20"
		>
			<Trash2 size={18} class="text-error shrink-0" />
			<span class="text-sm text-text flex-1">
				Delete "<span class="font-medium">{deletingPlaylist.playlist}</span>"?
				This cannot be undone.
			</span>
			<button
				class="px-3 py-1.5 rounded-lg bg-error text-white text-sm font-medium hover:bg-error/90 transition-colors disabled:opacity-50"
				onclick={confirmDelete}
				disabled={isDeleting}
			>
				{#if isDeleting}
					<Loader2 size={14} class="animate-spin inline" />
				{:else}
					Delete
				{/if}
			</button>
			<button
				class="px-3 py-1.5 rounded-lg bg-surface-0 text-text text-sm hover:bg-surface-1 transition-colors"
				onclick={cancelDelete}
			>
				Cancel
			</button>
		</div>
	{/if}

	<!-- Content -->
	<div class="flex-1 overflow-y-auto">
		{#if selectedPlaylist}
			<!-- ====== Playlist Detail / Track View ====== -->
			{#if isLoadingTracks}
				<div class="flex items-center justify-center py-16">
					<Loader2
						size={32}
						class="animate-spin dynamic-accent color-transition"
					/>
				</div>
			{:else if playlistTracks.length === 0}
				<div
					class="flex flex-col items-center justify-center h-full text-overlay-1 p-8"
				>
					<div
						class="w-20 h-20 rounded-full bg-surface-0 flex items-center justify-center mb-6"
					>
						<Music size={40} class="opacity-50" />
					</div>
					<h3 class="text-xl font-medium text-text mb-2">
						Playlist is empty
					</h3>
					<p class="text-sm text-center max-w-sm">
						This playlist has no tracks.
					</p>
				</div>
			{:else}
				<!-- Track Stats Bar -->
				<div
					class="flex items-center justify-between px-6 py-3 border-b border-border bg-surface-0/30"
				>
					<div class="flex items-center gap-3">
						<button
							class="flex items-center gap-2 px-5 py-2.5 rounded-full bg-accent text-crust font-medium
									   hover:bg-accent-hover hover:scale-105 active:scale-95 transition-all shadow-lg
									   disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100"
							onclick={() =>
								selectedPlaylist && handleLoadPlaylist(selectedPlaylist)}
							disabled={isActionInFlight}
							aria-label="Load and play"
						>
							<Play size={20} fill="currentColor" />
							<span>Play</span>
						</button>
					</div>

					<div class="text-sm text-overlay-1">
						<span
							>{tracksTotal}
							{tracksTotal === 1 ? 'track' : 'tracks'}</span
						>
						<span class="mx-2">•</span>
						<span>{getTotalDuration(playlistTracks)}</span>
					</div>
				</div>

				<!-- Track List Header -->
				<div
					class="grid grid-cols-[auto_1fr_auto] gap-4 px-4 py-2 text-xs text-overlay-1 uppercase tracking-wider border-b border-border"
				>
					<span class="w-8">#</span>
					<span>Title</span>
					<span class="flex items-center gap-1">
						<Clock size={14} />
					</span>
				</div>

				<!-- Track Rows -->
				{#each playlistTracks as track, index}
					<div
						class="group grid grid-cols-[auto_1fr_auto] gap-4 px-4 py-3 hover:bg-surface-0 transition-colors items-center"
					>
						<!-- Index -->
						<div class="w-8 flex items-center justify-center">
							<span class="text-overlay-1 text-sm">{index + 1}</span>
						</div>

						<!-- Track info -->
						<div class="min-w-0 flex flex-col gap-0.5">
							<span class="text-text truncate">{track.title}</span>
							<div
								class="flex items-center gap-1 text-sm text-overlay-1 truncate"
							>
								{#if track.artist}
									<span class="truncate">{track.artist}</span>
								{/if}
								{#if track.artist && track.album}
									<span>•</span>
								{/if}
								{#if track.album}
									<span class="truncate">{track.album}</span>
								{/if}
							</div>
						</div>

						<!-- Duration -->
						<span class="text-sm text-overlay-1 w-12 text-right">
							{formatDuration(track.duration)}
						</span>
					</div>
				{/each}
			{/if}
		{:else}
			<!-- ====== Playlist List View ====== -->
			{#if isLoading}
				<div class="flex items-center justify-center py-16">
					<Loader2
						size={32}
						class="animate-spin dynamic-accent color-transition"
					/>
				</div>
			{:else if playlists.length === 0}
				<div
					class="flex flex-col items-center justify-center h-full text-overlay-1 p-8"
				>
					<div
						class="w-20 h-20 rounded-full bg-surface-0 flex items-center justify-center mb-6"
					>
						<FolderOpen size={40} class="opacity-50" />
					</div>
					<h3 class="text-xl font-medium text-text mb-2">
						No saved playlists
					</h3>
					<p class="text-sm mb-6 text-center max-w-sm">
						Save your current queue as a playlist to access it later.
					</p>
					{#if playerStore.playlist.length > 0}
						<button
							class="px-4 py-2 bg-accent text-crust font-medium rounded-lg hover:bg-accent-hover transition-colors"
							onclick={openSaveDialog}
						>
							Save Current Queue
						</button>
					{/if}
				</div>
			{:else}
				<div class="flex flex-col py-2">
					{#each playlists as playlist}
						{@const isBeingRenamed = renamingId === playlist.id}

						<div
							class="group flex items-center gap-4 px-6 py-3 hover:bg-surface-0 transition-colors cursor-pointer"
							onclick={() => viewPlaylist(playlist)}
							onkeydown={(e) =>
								e.key === 'Enter' && viewPlaylist(playlist)}
							role="button"
							tabindex="0"
						>
							<!-- Icon -->
							<div
								class="w-10 h-10 rounded-lg bg-surface-1 flex items-center justify-center shrink-0 group-hover:bg-surface-2 transition-colors"
							>
								<ListMusic
									size={20}
									class="text-overlay-0 group-hover:text-accent transition-colors"
								/>
							</div>

							<!-- Name (or rename input) -->
							<div class="flex-1 min-w-0">
								{#if isBeingRenamed}
									<!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
									<div
										class="flex items-center gap-2"
										onclick={(e) => e.stopPropagation()}
									>
										<input
											bind:this={renameInput}
											type="text"
											bind:value={renameValue}
											onkeydown={handleRenameKeydown}
											class="flex-1 px-2 py-1 bg-surface-0 border border-accent rounded text-text text-sm
												   focus:outline-none focus:ring-1 focus:ring-accent"
										/>
										<button
											class="p-1 rounded hover:bg-surface-1 text-success transition-colors"
											onclick={confirmRename}
											aria-label="Confirm"
										>
											<Check size={16} />
										</button>
										<button
											class="p-1 rounded hover:bg-surface-1 text-overlay-1 transition-colors"
											onclick={cancelRename}
											aria-label="Cancel"
										>
											<X size={16} />
										</button>
									</div>
								{:else}
									<p class="text-text truncate font-medium text-sm">
										{playlist.playlist}
									</p>
									<p class="text-xs text-overlay-1 truncate">
										Saved playlist
									</p>
								{/if}
							</div>

							<!-- Actions (hover) -->
							{#if !isBeingRenamed}
								<div
									class="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
								>
									<!-- Quick Play -->
									<button
										class="p-2 rounded-full bg-accent/10 text-accent hover:bg-accent hover:text-crust transition-all"
										onclick={(e) => {
											e.stopPropagation();
											handleLoadPlaylist(playlist);
										}}
										aria-label="Load and play"
										title="Load and play this playlist"
									>
										<Play size={16} fill="currentColor" />
									</button>
									<!-- Rename -->
									<button
										class="p-1.5 rounded-full hover:bg-surface-1 text-overlay-1 hover:text-text transition-all"
										onclick={(e) => startRename(playlist, e)}
										aria-label="Rename"
										title="Rename playlist"
									>
										<Pencil size={14} />
									</button>
									<!-- Delete -->
									<button
										class="p-1.5 rounded-full hover:bg-surface-1 text-overlay-1 hover:text-error transition-all"
										onclick={(e) => startDelete(playlist, e)}
										aria-label="Delete"
										title="Delete playlist"
									>
										<Trash2 size={14} />
									</button>
								</div>

								<!-- Browse arrow -->
								<ChevronRight
									size={18}
									class="text-overlay-0 shrink-0 group-hover:text-text transition-colors"
								/>
							{/if}
						</div>
					{/each}
				</div>

				<!-- Footer Stats -->
				<div
					class="text-center text-xs text-overlay-0 py-4 border-t border-border"
				>
					{total}
					{total === 1 ? 'playlist' : 'playlists'}
				</div>
			{/if}
		{/if}
	</div>
</div>
