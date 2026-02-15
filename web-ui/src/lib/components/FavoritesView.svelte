<script lang="ts">
	import { playerStore } from '$lib/stores/player.svelte';
	import { toastStore } from '$lib/stores/toast.svelte';
	import { api, type FavoriteItem } from '$lib/api';
	import {
		Star,
		Play,
		Plus,
		Trash2,
		Pencil,
		FolderPlus,
		ChevronRight,
		ArrowLeft,
		Loader2,
		Music,
		Folder,
		Radio,
		Heart,
		X,
		Check,
	} from 'lucide-svelte';

	// ---------------------------------------------------------------------------
	// State
	// ---------------------------------------------------------------------------

	let items = $state<FavoriteItem[]>([]);
	let isLoading = $state(false);
	let total = $state(0);

	// Navigation stack for folder drill-down
	let folderStack = $state<{ id: string; name: string }[]>([]);
	let currentFolderId = $derived(
		folderStack.length > 0 ? folderStack[folderStack.length - 1].id : undefined,
	);
	let currentFolderName = $derived(
		folderStack.length > 0 ? folderStack[folderStack.length - 1].name : 'Favorites',
	);

	// Rename state
	let renamingId = $state<string | null>(null);
	let renameValue = $state('');
	let renameInput = $state<HTMLInputElement | null>(null);

	// Delete confirm state
	let deletingId = $state<string | null>(null);

	// Add folder state
	let showAddFolder = $state(false);
	let newFolderName = $state('');
	let addFolderInput = $state<HTMLInputElement | null>(null);

	// ---------------------------------------------------------------------------
	// Data loading
	// ---------------------------------------------------------------------------

	async function loadFavorites() {
		isLoading = true;
		try {
			const result = await api.getFavorites(0, 200, currentFolderId);
			items = result.items;
			total = result.total;
		} catch (err) {
			console.error('Failed to load favorites:', err);
			toastStore.error('Failed to load favorites', { detail: (err as Error).message });
		} finally {
			isLoading = false;
		}
	}

	// Reactive loading when folder changes
	$effect(() => {
		// Touch currentFolderId to re-run when it changes
		currentFolderId;
		loadFavorites();
	});

	// ---------------------------------------------------------------------------
	// Navigation
	// ---------------------------------------------------------------------------

	function enterFolder(item: FavoriteItem) {
		folderStack = [...folderStack, { id: item.id, name: item.name }];
	}

	function goBack() {
		if (folderStack.length > 0) {
			folderStack = folderStack.slice(0, -1);
		}
	}

	function goToRoot() {
		folderStack = [];
	}

	function goToLevel(index: number) {
		folderStack = folderStack.slice(0, index + 1);
	}

	// ---------------------------------------------------------------------------
	// Actions
	// ---------------------------------------------------------------------------

	async function handlePlay(item: FavoriteItem) {
		if (!playerStore.selectedPlayerId) {
			toastStore.warning('No player selected');
			return;
		}
		try {
			if (item.hasitems) {
				// Play all items in the folder
				await api.playFavorites(playerStore.selectedPlayerId, 'play', item.id);
				toastStore.success(`Playing "${item.name}"`);
			} else {
				// Play single item via playlist load
				await api.playFavorites(playerStore.selectedPlayerId, 'play', item.id);
				toastStore.success(`Playing "${item.name}"`);
			}
			await playerStore.loadStatus();
			await playerStore.loadPlaylist();
		} catch (err) {
			toastStore.error(`Failed to play "${item.name}"`, { detail: (err as Error).message });
		}
	}

	async function handleAdd(item: FavoriteItem, event: MouseEvent) {
		event.stopPropagation();
		if (!playerStore.selectedPlayerId) {
			toastStore.warning('No player selected');
			return;
		}
		try {
			await api.playFavorites(playerStore.selectedPlayerId, 'add', item.id);
			toastStore.success(`Added "${item.name}" to queue`);
			await playerStore.loadPlaylist();
		} catch (err) {
			toastStore.error(`Failed to add "${item.name}"`, { detail: (err as Error).message });
		}
	}

	function startRename(item: FavoriteItem, event: MouseEvent) {
		event.stopPropagation();
		renamingId = item.id;
		renameValue = item.name;
		// Focus input after render
		setTimeout(() => renameInput?.focus(), 50);
	}

	async function confirmRename() {
		if (!renamingId || !renameValue.trim()) {
			renamingId = null;
			return;
		}
		try {
			await api.renameFavorite(renamingId, renameValue.trim());
			toastStore.success('Favorite renamed');
			renamingId = null;
			await loadFavorites();
		} catch (err) {
			toastStore.error('Failed to rename favorite', { detail: (err as Error).message });
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

	function startDelete(item: FavoriteItem, event: MouseEvent) {
		event.stopPropagation();
		deletingId = item.id;
	}

	async function confirmDelete() {
		if (!deletingId) return;
		const item = items.find((i) => i.id === deletingId);
		const name = item?.name ?? deletingId;
		try {
			await api.deleteFavorite(deletingId);
			toastStore.success(`Deleted "${name}"`);
			deletingId = null;
			await loadFavorites();
		} catch (err) {
			toastStore.error(`Failed to delete "${name}"`, { detail: (err as Error).message });
		}
	}

	function cancelDelete() {
		deletingId = null;
	}

	function openAddFolder() {
		showAddFolder = true;
		newFolderName = '';
		setTimeout(() => addFolderInput?.focus(), 50);
	}

	async function confirmAddFolder() {
		if (!newFolderName.trim()) {
			showAddFolder = false;
			return;
		}
		try {
			await api.addFavoriteFolder(newFolderName.trim(), currentFolderId);
			toastStore.success(`Folder "${newFolderName.trim()}" created`);
			showAddFolder = false;
			newFolderName = '';
			await loadFavorites();
		} catch (err) {
			toastStore.error('Failed to create folder', { detail: (err as Error).message });
		}
	}

	function cancelAddFolder() {
		showAddFolder = false;
		newFolderName = '';
	}

	function handleAddFolderKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter') {
			confirmAddFolder();
		} else if (event.key === 'Escape') {
			cancelAddFolder();
		}
	}

	// ---------------------------------------------------------------------------
	// Helpers
	// ---------------------------------------------------------------------------

	function getItemIcon(item: FavoriteItem) {
		if (item.hasitems || item.type === 'folder') return Folder;
		if (item.type === 'audio' && item.url?.includes('radio')) return Radio;
		return Music;
	}
</script>

<div class="flex flex-col h-full">
	<!-- Header -->
	<div class="flex items-center justify-between px-6 py-4 border-b border-border bg-base/50 backdrop-blur-sm">
		<div class="flex items-center gap-3 min-w-0">
			{#if folderStack.length > 0}
				<button
					class="p-2 rounded-lg hover:bg-surface-0 text-overlay-1 hover:text-text transition-colors shrink-0"
					onclick={goBack}
					aria-label="Go back"
				>
					<ArrowLeft size={20} />
				</button>
			{/if}

			<!-- Breadcrumbs -->
			<nav class="flex items-center gap-1 text-sm overflow-hidden whitespace-nowrap">
				<button
					class="px-2 py-1 rounded hover:bg-surface-0 transition-colors text-overlay-1 hover:text-text flex items-center gap-1.5"
					onclick={goToRoot}
				>
					<Star size={16} class="shrink-0" />
					<span>Favorites</span>
				</button>
				{#each folderStack as crumb, index}
					<ChevronRight size={16} class="text-overlay-0 shrink-0" />
					<button
						class="px-2 py-1 rounded hover:bg-surface-0 transition-colors truncate max-w-[200px]
							{index === folderStack.length - 1 ? 'text-text font-medium' : 'text-overlay-1'}"
						onclick={() => goToLevel(index)}
					>
						{crumb.name}
					</button>
				{/each}
			</nav>
		</div>

		<!-- Actions -->
		<div class="flex items-center gap-2 shrink-0">
			<button
				class="flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-surface-0 text-overlay-1 hover:text-text transition-colors"
				onclick={openAddFolder}
				aria-label="New folder"
			>
				<FolderPlus size={18} />
				<span class="text-sm hidden sm:inline">New Folder</span>
			</button>
		</div>
	</div>

	<!-- Add Folder Inline Form -->
	{#if showAddFolder}
		<div class="flex items-center gap-3 px-6 py-3 bg-surface-0/50 border-b border-border">
			<FolderPlus size={18} class="text-accent shrink-0" />
			<input
				bind:this={addFolderInput}
				type="text"
				bind:value={newFolderName}
				onkeydown={handleAddFolderKeydown}
				placeholder="Folder name…"
				class="flex-1 px-3 py-1.5 bg-surface-0 border border-border rounded-lg text-text
					   placeholder:text-overlay-0 focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent text-sm"
			/>
			<button
				class="p-1.5 rounded-lg hover:bg-surface-1 text-success transition-colors"
				onclick={confirmAddFolder}
				aria-label="Create folder"
			>
				<Check size={18} />
			</button>
			<button
				class="p-1.5 rounded-lg hover:bg-surface-1 text-overlay-1 transition-colors"
				onclick={cancelAddFolder}
				aria-label="Cancel"
			>
				<X size={18} />
			</button>
		</div>
	{/if}

	<!-- Content -->
	<div class="flex-1 overflow-y-auto">
		{#if isLoading}
			<div class="flex items-center justify-center py-16">
				<Loader2 size={32} class="animate-spin dynamic-accent color-transition" />
			</div>
		{:else if items.length === 0}
			<div class="flex flex-col items-center justify-center h-full text-overlay-1 p-8">
				<div class="w-20 h-20 rounded-full bg-surface-0 flex items-center justify-center mb-6">
					<Heart size={40} class="opacity-50" />
				</div>
				<h3 class="text-xl font-medium text-text mb-2">
					{folderStack.length > 0 ? 'This folder is empty' : 'No favorites yet'}
				</h3>
				<p class="text-sm mb-4 text-center max-w-sm">
					{folderStack.length > 0
						? 'Add items to this folder from the library or radio.'
						: 'Add your favorite tracks, albums, and radio stations here for quick access.'}
				</p>
			</div>
		{:else}
			<!-- Favorites List -->
			<div class="flex flex-col py-2">
				{#each items as item}
					{@const isFolder = item.hasitems || item.type === 'folder'}
					{@const isBeingRenamed = renamingId === item.id}
					{@const isBeingDeleted = deletingId === item.id}
					{@const ItemIcon = getItemIcon(item)}

					<!-- Delete confirmation overlay -->
					{#if isBeingDeleted}
						<div class="flex items-center gap-3 px-6 py-3 bg-error/10 border-b border-error/20">
							<Trash2 size={18} class="text-error shrink-0" />
							<span class="text-sm text-text flex-1">
								Delete "<span class="font-medium">{item.name}</span>"?
							</span>
							<button
								class="px-3 py-1.5 rounded-lg bg-error text-white text-sm font-medium hover:bg-error/90 transition-colors"
								onclick={confirmDelete}
							>
								Delete
							</button>
							<button
								class="px-3 py-1.5 rounded-lg bg-surface-0 text-text text-sm hover:bg-surface-1 transition-colors"
								onclick={cancelDelete}
							>
								Cancel
							</button>
						</div>
					{:else}
						<div
							class="group flex items-center gap-4 px-6 py-3 hover:bg-surface-0 transition-colors cursor-pointer"
							onclick={() => isFolder ? enterFolder(item) : handlePlay(item)}
							onkeydown={(e) => e.key === 'Enter' && (isFolder ? enterFolder(item) : handlePlay(item))}
							role="button"
							tabindex="0"
						>
							<!-- Icon / Artwork -->
							<div class="w-10 h-10 rounded-lg bg-surface-1 flex items-center justify-center shrink-0 overflow-hidden group-hover:bg-surface-2 transition-colors">
								{#if item.icon}
									<img src={item.icon} alt="" class="w-full h-full object-cover" />
								{:else}
									<ItemIcon
										size={20}
										class="text-overlay-0 group-hover:text-accent transition-colors"
									/>
								{/if}
							</div>

							<!-- Name (or rename input) -->
							<div class="flex-1 min-w-0">
								{#if isBeingRenamed}
									<!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
									<div class="flex items-center gap-2" onclick={(e) => e.stopPropagation()}>
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
									<p class="text-text truncate font-medium text-sm">{item.name}</p>
									{#if item.url && !isFolder}
										<p class="text-xs text-overlay-1 truncate">{item.url}</p>
									{/if}
								{/if}
							</div>

							<!-- Folder arrow -->
							{#if isFolder}
								<ChevronRight size={18} class="text-overlay-0 shrink-0 group-hover:text-text transition-colors" />
							{/if}

							<!-- Action buttons (visible on hover) -->
							{#if !isBeingRenamed}
								<div class="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
									{#if !isFolder}
										<button
											class="p-1.5 rounded-full hover:bg-surface-1 text-overlay-1 hover:text-accent transition-all"
											onclick={(e) => handleAdd(item, e)}
											aria-label="Add to queue"
											title="Add to queue"
										>
											<Plus size={16} />
										</button>
									{/if}
									<button
										class="p-1.5 rounded-full hover:bg-surface-1 text-overlay-1 hover:text-text transition-all"
										onclick={(e) => startRename(item, e)}
										aria-label="Rename"
										title="Rename"
									>
										<Pencil size={14} />
									</button>
									<button
										class="p-1.5 rounded-full hover:bg-surface-1 text-overlay-1 hover:text-error transition-all"
										onclick={(e) => startDelete(item, e)}
										aria-label="Delete"
										title="Delete"
									>
										<Trash2 size={14} />
									</button>
								</div>
							{/if}
						</div>
					{/if}
				{/each}
			</div>

			<!-- Footer Stats -->
			<div class="text-center text-xs text-overlay-0 py-4 border-t border-border">
				{total} {total === 1 ? 'item' : 'items'}
				{#if folderStack.length > 0}
					in "{currentFolderName}"
				{/if}
			</div>
		{/if}
	</div>
</div>
