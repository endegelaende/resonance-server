<script lang="ts">
	import { playerStore } from '$lib/stores/player.svelte';
	import { toastStore } from '$lib/stores/toast.svelte';
	import { api, type PodcastItem } from '$lib/api';
	import {
		Podcast,
		Play,
		Plus,
		ChevronRight,
		ArrowLeft,
		Loader2,
		Search,
		X,
		Clock,
		Rss,
		BookOpen,
		Trash2,
		Check,
		History,
	} from 'lucide-svelte';

	// ---------------------------------------------------------------------------
	// State
	// ---------------------------------------------------------------------------

	let items = $state<PodcastItem[]>([]);
	let isLoading = $state(false);
	let total = $state(0);

	// Navigation stack for drill-down (subscriptions → episodes)
	let navStack = $state<{ url: string; name: string }[]>([]);
	let currentUrl = $derived(
		navStack.length > 0 ? navStack[navStack.length - 1].url : undefined,
	);
	let currentName = $derived(
		navStack.length > 0 ? navStack[navStack.length - 1].name : 'Podcasts',
	);

	// Search state
	let searchQuery = $state('');
	let isSearching = $state(false);
	let isSearchMode = $state(false);
	let searchResults = $state<PodcastItem[]>([]);
	let searchTotal = $state(0);
	let debounceTimer: ReturnType<typeof setTimeout> | null = null;
	let searchInput = $state<HTMLInputElement | null>(null);

	// In-flight guard
	let isActionInFlight = $state(false);

	// Unsubscribe confirmation
	let unsubscribingUrl = $state<string | null>(null);
	let unsubscribingName = $state('');

	// ---------------------------------------------------------------------------
	// Data loading
	// ---------------------------------------------------------------------------

	async function loadItems() {
		isLoading = true;
		try {
			const result = await api.getPodcastItems(0, 200, currentUrl);
			items = result.items;
			total = result.total;
		} catch (err) {
			console.error('Failed to load podcast items:', err);
			toastStore.error('Failed to load podcasts', { detail: (err as Error).message });
		} finally {
			isLoading = false;
		}
	}

	// Reactive loading when navigation changes
	$effect(() => {
		if (!isSearchMode) {
			currentUrl;
			loadItems();
		}
	});

	// ---------------------------------------------------------------------------
	// Search
	// ---------------------------------------------------------------------------

	function handleSearchInput(event: Event) {
		const target = event.target as HTMLInputElement;
		searchQuery = target.value;

		if (debounceTimer) clearTimeout(debounceTimer);

		if (searchQuery.length >= 2) {
			isSearching = true;
			isSearchMode = true;
			debounceTimer = setTimeout(() => {
				doSearch(searchQuery);
			}, 400);
		} else if (searchQuery.length === 0) {
			clearSearch();
		}
	}

	async function doSearch(query: string) {
		isSearching = true;
		try {
			const result = await api.searchPodcasts(query, 0, 200);
			searchResults = result.items;
			searchTotal = result.total;
		} catch (err) {
			console.error('Failed to search podcasts:', err);
			toastStore.error('Podcast search failed', { detail: (err as Error).message });
		} finally {
			isSearching = false;
		}
	}

	function clearSearch() {
		searchQuery = '';
		isSearchMode = false;
		isSearching = false;
		searchResults = [];
		searchTotal = 0;
	}

	function handleSearchKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			clearSearch();
			searchInput?.blur();
		} else if (event.key === 'Enter' && searchQuery.length >= 2) {
			if (debounceTimer) clearTimeout(debounceTimer);
			doSearch(searchQuery);
		}
	}

	// ---------------------------------------------------------------------------
	// Navigation
	// ---------------------------------------------------------------------------

	function enterItem(item: PodcastItem) {
		if (isSearchMode) {
			clearSearch();
		}
		navStack = [...navStack, { url: item.url, name: item.name }];
	}

	function goBack() {
		if (isSearchMode) {
			clearSearch();
			return;
		}
		if (navStack.length > 0) {
			navStack = navStack.slice(0, -1);
		}
	}

	function goToRoot() {
		clearSearch();
		navStack = [];
	}

	function goToLevel(index: number) {
		clearSearch();
		navStack = navStack.slice(0, index + 1);
	}

	// ---------------------------------------------------------------------------
	// Actions
	// ---------------------------------------------------------------------------

	async function handlePlay(item: PodcastItem) {
		if (isActionInFlight) return;
		if (!playerStore.selectedPlayerId) {
			toastStore.warning('No player selected');
			return;
		}

		isActionInFlight = true;
		try {
			await api.playPodcast(playerStore.selectedPlayerId, item.url, item.name, 'play');
			toastStore.success(`▶ Now playing "${item.name}"`, { detail: 'Queue replaced with this episode' });
			await Promise.all([playerStore.loadStatus(), playerStore.loadPlaylist()]);
		} catch (err) {
			toastStore.error(`Failed to play "${item.name}"`, { detail: (err as Error).message });
		} finally {
			isActionInFlight = false;
		}
	}

	async function handleAdd(item: PodcastItem, event: MouseEvent) {
		event.stopPropagation();
		if (isActionInFlight) return;
		if (!playerStore.selectedPlayerId) {
			toastStore.warning('No player selected');
			return;
		}

		isActionInFlight = true;
		try {
			await api.playPodcast(playerStore.selectedPlayerId, item.url, item.name, 'add');
			toastStore.success(`+ Added "${item.name}" to queue`, { detail: 'Episode appended — continues playing current track' });
			await playerStore.loadPlaylist();
		} catch (err) {
			toastStore.error(`Failed to add "${item.name}"`, { detail: (err as Error).message });
		} finally {
			isActionInFlight = false;
		}
	}

	async function handleSubscribe(item: PodcastItem, event: MouseEvent) {
		event.stopPropagation();
		if (isActionInFlight) return;

		isActionInFlight = true;
		try {
			await api.podcastSubscribe(item.url, item.name);
			toastStore.success(`Subscribed to "${item.name}"`);
			// If we're in search mode, don't reload — user may want to subscribe to more
			// If we're in browse mode at root, reload to show the new subscription
			if (!isSearchMode && navStack.length === 0) {
				await loadItems();
			}
		} catch (err) {
			toastStore.error(`Failed to subscribe to "${item.name}"`, {
				detail: (err as Error).message,
			});
		} finally {
			isActionInFlight = false;
		}
	}

	function startUnsubscribe(item: PodcastItem, event: MouseEvent) {
		event.stopPropagation();
		unsubscribingUrl = item.url;
		unsubscribingName = item.name;
	}

	async function confirmUnsubscribe() {
		if (!unsubscribingUrl) return;
		isActionInFlight = true;
		try {
			await api.podcastUnsubscribe(unsubscribingUrl);
			toastStore.success(`Unsubscribed from "${unsubscribingName}"`);
			unsubscribingUrl = null;
			unsubscribingName = '';
			// Go back to root if we were viewing episodes of the unsubscribed feed
			if (navStack.length > 0 && navStack[navStack.length - 1].url === unsubscribingUrl) {
				navStack = [];
			}
			await loadItems();
		} catch (err) {
			toastStore.error(`Failed to unsubscribe`, { detail: (err as Error).message });
		} finally {
			isActionInFlight = false;
		}
	}

	function cancelUnsubscribe() {
		unsubscribingUrl = null;
		unsubscribingName = '';
	}

	function handleItemClick(item: PodcastItem) {
		if (item.hasitems || item.type === 'folder' || item.type === 'link') {
			enterItem(item);
		} else if (item.type === 'audio') {
			handlePlay(item);
		} else if (item.type === 'search') {
			// Focus search input for "Search Podcasts" entry
			searchInput?.focus();
		} else {
			// Default: try to browse into it
			enterItem(item);
		}
	}

	// ---------------------------------------------------------------------------
	// Helpers
	// ---------------------------------------------------------------------------

	function isEpisode(item: PodcastItem): boolean {
		return item.type === 'audio' && !!item.url;
	}

	function isSubscription(item: PodcastItem): boolean {
		return (
			!isSearchMode &&
			navStack.length === 0 &&
			(item.type === 'folder' || item.hasitems) &&
			item.url !== '' &&
			item.url !== '__recent__' &&
			item.name !== 'Search Podcasts'
		);
	}

	function isRecentlyPlayed(item: PodcastItem): boolean {
		return item.url === '__recent__';
	}

	function isSearchEntry(item: PodcastItem): boolean {
		return item.type === 'search' || item.name === 'Search Podcasts';
	}

	let displayItems = $derived(isSearchMode ? searchResults : items);
	let displayTotal = $derived(isSearchMode ? searchTotal : total);
	let canGoBack = $derived(navStack.length > 0 || isSearchMode);

	// In episode view, we're one level deep in a feed
	let isEpisodeView = $derived(navStack.length > 0 && navStack[navStack.length - 1].url !== '__recent__');
	let isRecentView = $derived(navStack.length > 0 && navStack[navStack.length - 1].url === '__recent__');
</script>

<div class="flex flex-col h-full">
	<!-- Header -->
	<div
		class="flex items-center justify-between px-6 py-4 border-b border-border bg-base/50 backdrop-blur-sm"
	>
		<div class="flex items-center gap-3 min-w-0 flex-1">
			{#if canGoBack}
				<button
					class="p-2 rounded-lg hover:bg-surface-0 text-overlay-1 hover:text-text transition-colors shrink-0"
					onclick={goBack}
					aria-label="Go back"
				>
					<ArrowLeft size={20} />
				</button>
			{/if}

			<!-- Breadcrumbs -->
			{#if !isSearchMode}
				<nav class="flex items-center gap-1 text-sm overflow-hidden whitespace-nowrap">
					<button
						class="px-2 py-1 rounded hover:bg-surface-0 transition-colors text-overlay-1 hover:text-text flex items-center gap-1.5"
						onclick={goToRoot}
					>
						<Podcast size={16} class="shrink-0" />
						<span>Podcasts</span>
					</button>
					{#each navStack as crumb, index}
						<ChevronRight size={16} class="text-overlay-0 shrink-0" />
						<button
							class="px-2 py-1 rounded hover:bg-surface-0 transition-colors truncate max-w-[200px]
								{index === navStack.length - 1 ? 'text-text font-medium' : 'text-overlay-1'}"
							onclick={() => goToLevel(index)}
						>
							{crumb.name}
						</button>
					{/each}
				</nav>
			{:else}
				<div class="flex items-center gap-2 text-sm text-overlay-1">
					<Search size={16} />
					<span
						>Search results for "<span class="text-text font-medium">{searchQuery}</span
						>"</span
					>
				</div>
			{/if}
		</div>

		<!-- Search Input -->
		<div class="relative w-64 shrink-0 ml-4">
			<div class="absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none">
				{#if isSearching}
					<Loader2 size={16} class="text-overlay-1 animate-spin" />
				{:else}
					<Search size={16} class="text-overlay-1" />
				{/if}
			</div>
			<input
				bind:this={searchInput}
				type="text"
				value={searchQuery}
				oninput={handleSearchInput}
				onkeydown={handleSearchKeydown}
				placeholder="Search podcasts…"
				class="w-full pl-9 pr-8 py-2 bg-surface-0 border border-border rounded-lg text-sm
					   text-text placeholder:text-overlay-0
					   focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent transition-all"
			/>
			{#if searchQuery.length > 0}
				<button
					class="absolute right-2 top-1/2 -translate-y-1/2 p-0.5 rounded-full text-overlay-1 hover:text-text hover:bg-surface-1 transition-colors"
					onclick={clearSearch}
					aria-label="Clear search"
				>
					<X size={14} />
				</button>
			{/if}
		</div>
	</div>

	<!-- Unsubscribe Confirmation -->
	{#if unsubscribingUrl}
		<div
			class="flex items-center gap-3 px-6 py-3 bg-error/10 border-b border-error/20"
		>
			<Trash2 size={18} class="text-error shrink-0" />
			<span class="text-sm text-text flex-1">
				Unsubscribe from "<span class="font-medium">{unsubscribingName}</span>"?
			</span>
			<button
				class="px-3 py-1.5 rounded-lg bg-error text-white text-sm font-medium hover:bg-error/90 transition-colors"
				onclick={confirmUnsubscribe}
			>
				Unsubscribe
			</button>
			<button
				class="px-3 py-1.5 rounded-lg bg-surface-0 text-text text-sm hover:bg-surface-1 transition-colors"
				onclick={cancelUnsubscribe}
			>
				Cancel
			</button>
		</div>
	{/if}

	<!-- Content -->
	<div class="flex-1 overflow-y-auto">
		{#if isLoading || isSearching}
			<div class="flex items-center justify-center py-16">
				<Loader2 size={32} class="animate-spin dynamic-accent color-transition" />
			</div>
		{:else if displayItems.length === 0}
			<div
				class="flex flex-col items-center justify-center h-full text-overlay-1 p-8"
			>
				<div
					class="w-20 h-20 rounded-full bg-surface-0 flex items-center justify-center mb-6"
				>
					{#if isSearchMode}
						<Search size={40} class="opacity-50" />
					{:else}
						<Podcast size={40} class="opacity-50" />
					{/if}
				</div>
				<h3 class="text-xl font-medium text-text mb-2">
					{#if isSearchMode}
						No podcasts found
					{:else if isEpisodeView}
						No episodes available
					{:else}
						No podcast subscriptions
					{/if}
				</h3>
				<p class="text-sm text-center max-w-sm">
					{#if isSearchMode}
						Try a different search term to find podcasts on PodcastIndex.
					{:else if isEpisodeView}
						This feed doesn't have any episodes yet.
					{:else}
						Search for podcasts to subscribe, or the Podcast plugin may not be loaded.
					{/if}
				</p>
			</div>
		{:else}
			<!-- Items List -->
			<div class="flex flex-col py-2">
				{#each displayItems as item}
					{@const episode = isEpisode(item)}
					{@const subscription = isSubscription(item)}
					{@const recent = isRecentlyPlayed(item)}
					{@const searchEntry = isSearchEntry(item)}
					{@const isBrowsable =
						item.hasitems || item.type === 'folder' || item.type === 'link'}

					<div
						class="group flex items-center gap-4 px-6 py-3 hover:bg-surface-0 transition-colors cursor-pointer
							   {isActionInFlight ? 'pointer-events-none opacity-70' : ''}"
						onclick={() => handleItemClick(item)}
						onkeydown={(e) => e.key === 'Enter' && handleItemClick(item)}
						role="button"
						tabindex="0"
					>
						<!-- Icon / Artwork -->
						<div
							class="w-12 h-12 rounded-lg bg-surface-1 flex items-center justify-center shrink-0 overflow-hidden group-hover:bg-surface-2 transition-colors"
						>
							{#if item.icon}
								<img
									src={item.icon}
									alt=""
									class="w-full h-full object-cover"
									onerror={(e) => {
										(e.target as HTMLImageElement).style.display = 'none';
									}}
								/>
							{:else if searchEntry}
								<Search
									size={22}
									class="text-overlay-0 group-hover:text-accent transition-colors"
								/>
							{:else if recent}
								<History
									size={22}
									class="text-overlay-0 group-hover:text-accent transition-colors"
								/>
							{:else if episode}
								<BookOpen
									size={22}
									class="text-overlay-0 group-hover:text-accent transition-colors"
								/>
							{:else}
								<Rss
									size={22}
									class="text-overlay-0 group-hover:text-accent transition-colors"
								/>
							{/if}
						</div>

						<!-- Info -->
						<div class="flex-1 min-w-0">
							<p class="text-text truncate font-medium text-sm">{item.name}</p>
							{#if item.subtitle}
								<p class="text-xs text-overlay-1 truncate mt-0.5">
									{item.subtitle}
								</p>
							{/if}
						</div>

						<!-- Actions -->
						{#if episode}
							<!-- Episode: play + add actions -->
							<div
								class="flex items-center gap-1 shrink-0"
							>
								<!-- Add to queue (appends, does not interrupt current playback) -->
								<button
									class="flex items-center gap-1 px-2 py-1.5 rounded-lg
										   bg-surface-1 text-overlay-1
										   hover:bg-surface-2 hover:text-text
										   opacity-0 group-hover:opacity-100 transition-all text-xs"
									onclick={(e) => handleAdd(item, e)}
									aria-label="Add to queue"
									title="Add to queue (keeps playing current track)"
								>
									<Plus size={14} />
									<span>Queue</span>
								</button>
								<!-- Play now (replaces queue and starts this episode) -->
								<button
									class="flex items-center gap-1 px-2.5 py-1.5 rounded-lg
										   bg-accent/10 text-accent
										   hover:bg-accent hover:text-crust
										   transition-all text-xs font-medium"
									onclick={(e) => {
										e.stopPropagation();
										handlePlay(item);
									}}
									aria-label="Play now"
									title="Play now (replaces queue)"
								>
									<Play size={14} fill="currentColor" />
									<span>Play</span>
								</button>
							</div>
						{:else if isSearchMode && isBrowsable}
							<!-- Search result (a feed): subscribe + browse -->
							<div
								class="flex items-center gap-2 shrink-0"
							>
								<button
									class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent/10 text-accent hover:bg-accent hover:text-crust text-xs font-medium transition-all
										   opacity-0 group-hover:opacity-100"
									onclick={(e) => handleSubscribe(item, e)}
									aria-label="Subscribe"
									title="Subscribe to this podcast"
								>
									<Plus size={14} />
									<span>Subscribe</span>
								</button>
								<ChevronRight
									size={18}
									class="text-overlay-0 group-hover:text-text transition-colors"
								/>
							</div>
						{:else if subscription}
							<!-- Subscription at root: unsubscribe + browse -->
							<div
								class="flex items-center gap-1 shrink-0"
							>
								<button
									class="p-1.5 rounded-full hover:bg-surface-1 text-overlay-1 hover:text-error transition-all
										   opacity-0 group-hover:opacity-100"
									onclick={(e) => startUnsubscribe(item, e)}
									aria-label="Unsubscribe"
									title="Unsubscribe"
								>
									<Trash2 size={14} />
								</button>
								<ChevronRight
									size={18}
									class="text-overlay-0 group-hover:text-text transition-colors"
								/>
							</div>
						{:else if isBrowsable}
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
				{displayTotal}
				{displayTotal === 1 ? 'item' : 'items'}
				{#if isSearchMode}
					matching "{searchQuery}"
				{:else if isEpisodeView}
					in "{currentName}"
				{:else if isRecentView}
					recently played
				{/if}
			</div>
		{/if}
	</div>
</div>
