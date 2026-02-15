<script lang="ts">
	import { playerStore } from '$lib/stores/player.svelte';
	import { toastStore } from '$lib/stores/toast.svelte';
	import { api, type RadioItem } from '$lib/api';
	import {
		Radio,
		Play,
		ChevronRight,
		ArrowLeft,
		Loader2,
		Search,
		X,
		Globe,
		TrendingUp,
		Tag,
		Languages,
		Heart,
		Star,
	} from 'lucide-svelte';

	// ---------------------------------------------------------------------------
	// State
	// ---------------------------------------------------------------------------

	let items = $state<RadioItem[]>([]);
	let isLoading = $state(false);
	let total = $state(0);

	// Navigation stack for category drill-down.
	let navStack = $state<{ category: string; name: string }[]>([]);
	let currentCategory = $derived(
		navStack.length > 0 ? navStack[navStack.length - 1].category : undefined,
	);
	let currentName = $derived(
		navStack.length > 0 ? navStack[navStack.length - 1].name : 'Radio',
	);

	// Search state
	let searchQuery = $state('');
	let isSearching = $state(false);
	let isSearchMode = $state(false);
	let searchResults = $state<RadioItem[]>([]);
	let searchTotal = $state(0);
	let debounceTimer: ReturnType<typeof setTimeout> | null = null;
	let searchInput = $state<HTMLInputElement | null>(null);

	// In-flight guard
	let isActionInFlight = $state(false);

	// ---------------------------------------------------------------------------
	// Category definitions for hero cards
	// ---------------------------------------------------------------------------

	interface CategoryCard {
		key: string;
		name: string;
		description: string;
		icon: typeof Radio;
		gradient: string;
		iconBg: string;
	}

	const categoryCards: CategoryCard[] = [
		{
			key: 'popular',
			name: 'Popular Stations',
			description: 'Top-rated stations by the community',
			icon: Star,
			gradient: 'from-amber-500/20 via-orange-500/10 to-transparent',
			iconBg: 'bg-amber-500/20 text-amber-400',
		},
		{
			key: 'trending',
			name: 'Trending Now',
			description: 'Most listened to right now',
			icon: TrendingUp,
			gradient: 'from-rose-500/20 via-pink-500/10 to-transparent',
			iconBg: 'bg-rose-500/20 text-rose-400',
		},
		{
			key: 'country',
			name: 'By Country',
			description: 'Explore radio from around the world',
			icon: Globe,
			gradient: 'from-sky-500/20 via-blue-500/10 to-transparent',
			iconBg: 'bg-sky-500/20 text-sky-400',
		},
		{
			key: 'tag',
			name: 'By Genre',
			description: 'Jazz, Rock, Classical, and more',
			icon: Tag,
			gradient: 'from-violet-500/20 via-purple-500/10 to-transparent',
			iconBg: 'bg-violet-500/20 text-violet-400',
		},
		{
			key: 'language',
			name: 'By Language',
			description: 'Find stations in your language',
			icon: Languages,
			gradient: 'from-emerald-500/20 via-teal-500/10 to-transparent',
			iconBg: 'bg-emerald-500/20 text-emerald-400',
		},
	];

	// Smaller icon map for breadcrumbs / subcategory lists
	const categoryIconMap: Record<string, typeof Radio> = {
		popular: Star,
		trending: TrendingUp,
		country: Globe,
		tag: Tag,
		language: Languages,
	};

	// ---------------------------------------------------------------------------
	// Data loading
	// ---------------------------------------------------------------------------

	async function loadItems() {
		isLoading = true;
		try {
			const result = await api.getRadioItems(0, 500, currentCategory);
			items = result.items;
			total = result.total;
		} catch (err) {
			console.error('Failed to load radio items:', err);
			toastStore.error('Failed to load radio', { detail: (err as Error).message });
		} finally {
			isLoading = false;
		}
	}

	// Reactive loading when navigation changes
	$effect(() => {
		if (!isSearchMode) {
			// Touch currentCategory so Svelte tracks it
			void currentCategory;
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
			const result = await api.searchRadio(query, 0, 500);
			searchResults = result.items;
			searchTotal = result.total;
		} catch (err) {
			console.error('Failed to search radio:', err);
			toastStore.error('Radio search failed', { detail: (err as Error).message });
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

	function enterCategoryByKey(key: string, name: string) {
		if (isSearchMode) clearSearch();
		navStack = [...navStack, { category: key, name: stripCount(name) }];
	}

	function enterCategory(item: RadioItem) {
		// Use the category field from the backend (e.g. "popular", "country:DE")
		const key = item.category || item.url || item.name;
		enterCategoryByKey(key, item.name);
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

	/** Strip trailing " (123)" count from category names for breadcrumbs. */
	function stripCount(name: string): string {
		return name.replace(/\s*\(\d[\d,]*\)\s*$/, '');
	}

	// ---------------------------------------------------------------------------
	// Actions
	// ---------------------------------------------------------------------------

	async function handlePlay(item: RadioItem) {
		if (isActionInFlight) return;
		if (!playerStore.selectedPlayerId) {
			toastStore.warning('No player selected');
			return;
		}

		isActionInFlight = true;
		try {
			await api.playRadio(playerStore.selectedPlayerId, item.url, item.name, 'play', {
				icon: item.icon,
				codec: item.codec,
				bitrate: item.bitrate,
				stationuuid: item.stationuuid,
			});
			toastStore.success(`Now playing "${item.name}"`, { detail: 'Queue replaced with this station' });
			await Promise.all([playerStore.loadStatus(), playerStore.loadPlaylist()]);
		} catch (err) {
			toastStore.error(`Failed to play "${item.name}"`, { detail: (err as Error).message });
		} finally {
			isActionInFlight = false;
		}
	}

	function handleItemClick(item: RadioItem) {
		if (item.hasitems || item.type === 'link') {
			enterCategory(item);
		} else {
			handlePlay(item);
		}
	}

	// ---------------------------------------------------------------------------
	// Helpers
	// ---------------------------------------------------------------------------

	function isPlayable(item: RadioItem): boolean {
		return item.type === 'audio' || (!item.hasitems && item.type !== 'link');
	}

	/** Get tags as a trimmed array (max 3). */
	function getTagPills(item: RadioItem): string[] {
		if (!item.tags) return [];
		return item.tags
			.split(',')
			.map((t) => t.trim())
			.filter(Boolean)
			.slice(0, 3);
	}

	/** Format vote count for display (e.g. 1200 → "1.2k"). */
	function formatVotes(votes?: number): string {
		if (!votes) return '';
		if (votes >= 1_000_000) return `${(votes / 1_000_000).toFixed(1)}M`;
		if (votes >= 1_000) return `${(votes / 1_000).toFixed(1)}k`;
		return String(votes);
	}

	/** Build a subtitle string. */
	function stationSubtitle(item: RadioItem): string {
		if (item.subtext) return item.subtext;
		const parts: string[] = [];
		if (item.codec && item.bitrate) {
			parts.push(`${item.codec} ${item.bitrate}kbps`);
		} else if (item.codec) {
			parts.push(item.codec);
		} else if (item.bitrate) {
			parts.push(`${item.bitrate}kbps`);
		}
		if (item.country) parts.push(item.country);
		return parts.join(' · ');
	}

	/** Deterministic gradient from station name for favicon fallback. */
	function stationGradient(name: string): string {
		let hash = 0;
		for (let i = 0; i < name.length; i++) {
			hash = name.charCodeAt(i) + ((hash << 5) - hash);
		}
		const h1 = Math.abs(hash % 360);
		const h2 = (h1 + 40) % 360;
		return `linear-gradient(135deg, hsl(${h1}, 60%, 30%) 0%, hsl(${h2}, 50%, 20%) 100%)`;
	}

	/** Get first letter(s) for favicon fallback. */
	function stationInitials(name: string): string {
		const words = name.trim().split(/\s+/);
		if (words.length >= 2) {
			return (words[0][0] + words[1][0]).toUpperCase();
		}
		return name.substring(0, 2).toUpperCase();
	}

	let displayItems = $derived(isSearchMode ? searchResults : items);
	let displayTotal = $derived(isSearchMode ? searchTotal : total);
	let canGoBack = $derived(navStack.length > 0 || isSearchMode);

	/** True when at the top-level category grid (not drilled into anything). */
	let isTopLevel = $derived(!isSearchMode && navStack.length === 0);

	/** True when viewing a station list (not sub-category folders). */
	let isStationList = $derived(
		isSearchMode ||
			(displayItems.length > 0 && displayItems.some((i) => i.type === 'audio')),
	);

	/** Icon for the current top-level category (for the header). */
	let currentCategoryIcon = $derived.by(() => {
		if (!navStack.length) return Radio;
		const rootCat = navStack[0].category;
		return categoryIconMap[rootCat] || Globe;
	});
</script>

<div class="flex flex-col h-full">
	<!-- ===================================================================== -->
	<!-- Header                                                                 -->
	<!-- ===================================================================== -->
	<div
		class="flex items-center justify-between gap-4 px-6 py-4 border-b border-border
		       bg-base/60 backdrop-blur-md sticky top-0 z-10"
	>
		<div class="flex items-center gap-3 min-w-0 flex-1">
			{#if canGoBack}
				<button
					class="p-2 rounded-xl hover:bg-surface-0 text-overlay-1 hover:text-text
					       transition-all duration-200 shrink-0 active:scale-95"
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
						class="px-2.5 py-1.5 rounded-lg hover:bg-surface-0 transition-all text-overlay-1
						       hover:text-text flex items-center gap-2 shrink-0"
						onclick={goToRoot}
					>
						<Radio size={16} class="shrink-0 {navStack.length === 0 ? 'text-accent' : ''}" />
						<span class={navStack.length === 0 ? 'font-semibold text-text' : ''}>Radio</span>
					</button>
					{#each navStack as crumb, index}
						<ChevronRight size={14} class="text-overlay-0/50 shrink-0" />
						{#if index === navStack.length - 1}
							<span class="px-2.5 py-1.5 font-semibold text-text truncate max-w-[220px]">
								{crumb.name}
							</span>
						{:else}
							<button
								class="px-2.5 py-1.5 rounded-lg hover:bg-surface-0 transition-all
								       text-overlay-1 hover:text-text truncate max-w-[160px]"
								onclick={() => goToLevel(index)}
							>
								{crumb.name}
							</button>
						{/if}
					{/each}
				</nav>
			{:else}
				<div class="flex items-center gap-2.5 text-sm">
					<div class="p-1.5 rounded-lg bg-accent/10">
						<Search size={14} class="text-accent" />
					</div>
					<span class="text-overlay-1">
						Results for "<span class="text-text font-medium">{searchQuery}</span>"
					</span>
					{#if !isSearching}
						<span class="text-overlay-0 text-xs">({searchTotal})</span>
					{/if}
				</div>
			{/if}
		</div>

		<!-- Search Input -->
		<div class="relative w-72 shrink-0">
			<div class="absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none">
				{#if isSearching}
					<Loader2 size={16} class="text-accent animate-spin" />
				{:else}
					<Search size={16} class="text-overlay-0" />
				{/if}
			</div>
			<input
				bind:this={searchInput}
				type="text"
				value={searchQuery}
				oninput={handleSearchInput}
				onkeydown={handleSearchKeydown}
				placeholder="Search stations…"
				class="w-full pl-10 pr-9 py-2.5 bg-surface-0/60 border border-border/60 rounded-xl
				       text-sm text-text placeholder:text-overlay-0
				       focus:outline-none focus:border-accent/60 focus:ring-2 focus:ring-accent/20
				       focus:bg-surface-0 transition-all duration-200"
			/>
			{#if searchQuery.length > 0}
				<button
					class="absolute right-2.5 top-1/2 -translate-y-1/2 p-1 rounded-full
					       text-overlay-0 hover:text-text hover:bg-surface-1 transition-all"
					onclick={clearSearch}
					aria-label="Clear search"
				>
					<X size={14} />
				</button>
			{/if}
		</div>
	</div>

	<!-- ===================================================================== -->
	<!-- Content                                                                -->
	<!-- ===================================================================== -->
	<div class="flex-1 overflow-y-auto">
		{#if isLoading || (isSearching && searchResults.length === 0)}
			<!-- Loading state -->
			<div class="flex flex-col items-center justify-center py-24 gap-4">
				<div class="relative">
					<div class="w-12 h-12 rounded-full border-2 border-accent/20 border-t-accent animate-spin"></div>
				</div>
				<p class="text-sm text-overlay-1 animate-pulse">
					{isSearching ? 'Searching stations…' : 'Loading radio…'}
				</p>
			</div>

		{:else if isTopLevel && !isSearchMode}
			<!-- ============================================================= -->
			<!-- TOP LEVEL — Category Hero Cards                                -->
			<!-- ============================================================= -->
			<div class="p-6 space-y-6">
				<!-- Hero header -->
				<div class="flex items-center gap-4 mb-2">
					<div class="p-3 rounded-2xl bg-accent/10">
						<Radio size={28} class="text-accent" />
					</div>
					<div>
						<h1 class="text-2xl font-bold text-text">Internet Radio</h1>
						<p class="text-sm text-overlay-1 mt-0.5">
							Browse 40,000+ stations from around the world
						</p>
					</div>
				</div>

				<!-- Category cards grid -->
				<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
					{#each categoryCards as card}
						<button
							class="group relative overflow-hidden rounded-2xl border border-border/50
							       bg-surface-0/40 hover:bg-surface-0/70 backdrop-blur-sm
							       text-left transition-all duration-300 hover:border-border
							       hover:shadow-lg hover:shadow-black/20 hover:-translate-y-0.5
							       active:translate-y-0 active:shadow-md"
							onclick={() => enterCategoryByKey(card.key, card.name)}
						>
							<!-- Gradient overlay -->
							<div
								class="absolute inset-0 bg-gradient-to-br {card.gradient}
								       opacity-60 group-hover:opacity-100 transition-opacity duration-300"
							></div>

							<div class="relative p-5 flex items-start gap-4">
								<div
									class="p-3 rounded-xl {card.iconBg} shrink-0
									       group-hover:scale-110 transition-transform duration-300"
								>
									<card.icon size={22} />
								</div>
								<div class="min-w-0">
									<h3 class="font-semibold text-text text-[15px] mb-1 group-hover:text-accent transition-colors">
										{card.name}
									</h3>
									<p class="text-xs text-overlay-1 leading-relaxed">
										{card.description}
									</p>
								</div>
								<ChevronRight
									size={18}
									class="text-overlay-0 shrink-0 mt-1 opacity-0 -translate-x-2
									       group-hover:opacity-100 group-hover:translate-x-0
									       transition-all duration-300"
								/>
							</div>
						</button>
					{/each}
				</div>

				<!-- Attribution -->
				<div class="flex items-center justify-center gap-2 pt-4 text-xs text-overlay-0/60">
					<Globe size={12} />
					<span>Powered by</span>
					<a
						href="https://www.radio-browser.info"
						target="_blank"
						rel="noopener noreferrer"
						class="underline decoration-dotted underline-offset-2 hover:text-overlay-1 transition-colors"
					>
						radio-browser.info
					</a>
					<span>— free &amp; open community database</span>
				</div>
			</div>

		{:else if displayItems.length === 0}
			<!-- ============================================================= -->
			<!-- Empty state                                                    -->
			<!-- ============================================================= -->
			<div class="flex flex-col items-center justify-center h-full text-overlay-1 p-8">
				<div
					class="w-20 h-20 rounded-2xl bg-surface-0/60 flex items-center justify-center mb-6
					       border border-border/30"
				>
					{#if isSearchMode}
						<Search size={36} class="opacity-40" />
					{:else}
						<Radio size={36} class="opacity-40" />
					{/if}
				</div>
				<h3 class="text-lg font-semibold text-text mb-2">
					{#if isSearchMode}
						No stations found
					{:else}
						No stations here
					{/if}
				</h3>
				<p class="text-sm text-center max-w-sm leading-relaxed">
					{#if isSearchMode}
						Try a different search term — we're searching over 40,000 stations.
					{:else}
						This category appears to be empty. Try going back and exploring another category.
					{/if}
				</p>
				{#if isSearchMode}
					<button
						class="mt-6 px-4 py-2 rounded-xl bg-surface-0 hover:bg-surface-1
						       text-sm text-text border border-border/50 transition-all"
						onclick={clearSearch}
					>
						Clear search
					</button>
				{/if}
			</div>

		{:else if isStationList}
			<!-- ============================================================= -->
			<!-- Station list                                                   -->
			<!-- ============================================================= -->
			<div class="py-2">
				{#each displayItems as item, idx (item.stationuuid || item.url || idx)}
					{@const playable = isPlayable(item)}
					{@const subtitle = stationSubtitle(item)}
					{@const tags = getTagPills(item)}
					{@const votes = formatVotes(item.votes)}

					{#if playable}
						<!-- Station row -->
						<div
							class="group flex items-center gap-4 px-6 py-3 hover:bg-surface-0/60
							       transition-all duration-200 cursor-pointer
							       {isActionInFlight ? 'pointer-events-none opacity-60' : ''}"
							onclick={() => handlePlay(item)}
							onkeydown={(e) => e.key === 'Enter' && handlePlay(item)}
							role="button"
							tabindex="0"
						>
							<!-- Station favicon with play overlay -->
							<div
								class="w-11 h-11 rounded-xl shrink-0 overflow-hidden shadow-md
								       shadow-black/20 group-hover:shadow-lg group-hover:shadow-black/30
								       transition-shadow duration-300 relative"
							>
								{#if item.icon}
									<img
										src={item.icon}
										alt=""
										class="w-full h-full object-cover"
										loading="lazy"
										onerror={(e) => {
											const img = e.target as HTMLImageElement;
											img.style.display = 'none';
											const fallback = img.nextElementSibling as HTMLElement;
											if (fallback) fallback.style.display = 'flex';
										}}
									/>
									<div
										class="w-full h-full items-center justify-center text-xs font-bold
										       text-white/90 tracking-wide hidden"
										style="background: {stationGradient(item.name)}"
									>
										{stationInitials(item.name)}
									</div>
								{:else}
									<div
										class="w-full h-full flex items-center justify-center text-xs font-bold
										       text-white/90 tracking-wide"
										style="background: {stationGradient(item.name)}"
									>
										{stationInitials(item.name)}
									</div>
								{/if}
								<!-- Play overlay on hover -->
								<div
									class="absolute inset-0 bg-black/50 flex items-center justify-center
									       opacity-0 group-hover:opacity-100 transition-opacity duration-200"
								>
									<Play size={18} class="text-white ml-0.5" fill="currentColor" />
								</div>
							</div>

							<!-- Station info -->
							<div class="flex-1 min-w-0">
								<div class="flex items-center gap-2 mb-0.5">
									<p class="text-[14px] text-text truncate font-medium leading-tight">
										{item.name}
									</p>
									<!-- LIVE pulse -->
									<div class="flex items-center gap-1 shrink-0" title="Live stream">
										<span class="relative flex h-2 w-2">
											<span
												class="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-50"
											></span>
											<span class="relative inline-flex rounded-full h-2 w-2 bg-red-500"></span>
										</span>
									</div>
								</div>

								<!-- Subtitle -->
								{#if subtitle}
									<p class="text-xs text-overlay-1 truncate leading-relaxed">{subtitle}</p>
								{/if}

								<!-- Tag pills -->
								{#if tags.length > 0}
									<div class="flex items-center gap-1.5 mt-1.5 flex-wrap">
										{#each tags as tag}
											<span
												class="inline-flex px-2 py-0.5 rounded-full text-[10px] font-medium
												       bg-surface-1/80 text-overlay-1 border border-border/30
												       group-hover:bg-surface-2/60 group-hover:text-text
												       transition-colors duration-200 leading-tight"
											>
												{tag}
											</span>
										{/each}
										{#if votes}
											<span
												class="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full
												       text-[10px] text-overlay-0 leading-tight"
												title="{item.votes} community votes"
											>
												<Heart size={9} class="fill-current" />
												{votes}
											</span>
										{/if}
									</div>
								{/if}
							</div>

							<!-- Codec / bitrate badge -->
							{#if item.codec || item.bitrate}
								<div
									class="hidden sm:flex flex-col items-end gap-0.5 shrink-0 mr-1
									       text-[11px] text-overlay-0 opacity-60 group-hover:opacity-100
									       transition-opacity"
								>
									{#if item.codec}
										<span class="font-mono font-medium">{item.codec}</span>
									{/if}
									{#if item.bitrate}
										<span>{item.bitrate}kbps</span>
									{/if}
								</div>
							{/if}


						</div>
					{:else}
						<!-- This is a sub-category item within a station list (rare but possible) -->
						<div
							class="group flex items-center gap-4 px-6 py-3 hover:bg-surface-0/60
							       transition-all duration-200 cursor-pointer"
							onclick={() => enterCategory(item)}
							onkeydown={(e) => e.key === 'Enter' && enterCategory(item)}
							role="button"
							tabindex="0"
						>
							<div
								class="w-11 h-11 rounded-xl bg-surface-1/60 flex items-center justify-center
								       shrink-0 border border-border/30
								       group-hover:bg-surface-2/60 group-hover:border-border/60
								       transition-all duration-200"
							>
								<Globe size={18} class="text-overlay-0 group-hover:text-accent transition-colors" />
							</div>
							<div class="flex-1 min-w-0">
								<p class="text-[14px] text-text truncate font-medium">{item.name}</p>
							</div>
							<ChevronRight
								size={16}
								class="text-overlay-0 shrink-0 group-hover:text-text
								       group-hover:translate-x-0.5 transition-all duration-200"
							/>
						</div>
					{/if}
				{/each}
			</div>

		{:else}
			<!-- ============================================================= -->
			<!-- Sub-category folder list (countries, tags, languages)          -->
			<!-- ============================================================= -->
			{#if true}
			{@const CatIcon = currentCategoryIcon}
			<div class="py-2">
				<!-- Count header -->
				<div class="px-6 py-3 flex items-center gap-2">
					<CatIcon size={16} class="text-overlay-0" />
					<span class="text-xs text-overlay-0 font-medium uppercase tracking-wider">
						{displayTotal}
						{#if currentCategory === 'country'}
							Countries
						{:else if currentCategory === 'tag'}
							Genres
						{:else if currentCategory === 'language'}
							Languages
						{:else}
							Items
						{/if}
					</span>
				</div>

				<!-- Sub-category list -->
				<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-2 gap-y-0 px-2">
					{#each displayItems as item, idx (item.category || item.url || idx)}
						<button
							class="group flex items-center gap-3 px-4 py-2.5 rounded-xl
							       hover:bg-surface-0/60 transition-all duration-200 text-left mx-0"
							onclick={() => enterCategory(item)}
						>
							<div
								class="w-8 h-8 rounded-lg bg-surface-1/50 flex items-center justify-center
								       shrink-0 group-hover:bg-surface-2/60 transition-colors"
							>
								{#if currentCategory === 'country' && item.name}
									<!-- Show country code as mini-flag placeholder -->
									<span class="text-xs font-bold text-overlay-0 group-hover:text-accent transition-colors">
										{item.name.match(/\((\d+)\)/) ? '' : ''}{item.name.split(' ')[0].substring(0, 2).toUpperCase()}
									</span>
								{:else}
									<CatIcon
										size={14}
										class="text-overlay-0 group-hover:text-accent transition-colors"
									/>
								{/if}
							</div>
							<span
								class="text-sm text-text truncate flex-1 group-hover:text-accent transition-colors"
							>
								{item.name}
							</span>
							<ChevronRight
								size={14}
								class="text-overlay-0/40 shrink-0 opacity-0 group-hover:opacity-100
								       group-hover:translate-x-0.5 transition-all duration-200"
							/>
						</button>
					{/each}
				</div>
			</div>
			{/if}
		{/if}

		<!-- ================================================================= -->
		<!-- Footer                                                             -->
		<!-- ================================================================= -->
		{#if !isTopLevel && displayItems.length > 0}
			<div
				class="flex items-center justify-center gap-2 text-xs text-overlay-0/50 py-5
				       border-t border-border/30 mx-6"
			>
				<span>
					{displayTotal}
					{isStationList ? (displayTotal === 1 ? 'station' : 'stations') : (displayTotal === 1 ? 'item' : 'items')}
				</span>
				{#if isSearchMode}
					<span>matching "{searchQuery}"</span>
				{:else if currentName !== 'Radio'}
					<span>in {currentName}</span>
				{/if}
				<span class="mx-0.5">·</span>
				<Globe size={10} />
				<span>radio-browser.info</span>
			</div>
		{/if}
	</div>
</div>

<style>
	/* Subtle entrance animation for station rows */
	@keyframes fade-slide-in {
		from {
			opacity: 0;
			transform: translateY(4px);
		}
		to {
			opacity: 1;
			transform: translateY(0);
		}
	}

	/* Category cards — subtle scale on active */
	button:active {
		transition-duration: 100ms;
	}
</style>
