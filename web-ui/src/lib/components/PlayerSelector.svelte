<script lang="ts">
	import { playerStore } from '$lib/stores/player.svelte';
	import { Speaker, ChevronDown, Wifi, WifiOff } from 'lucide-svelte';

	let isOpen = $state(false);

	function toggleDropdown() {
		isOpen = !isOpen;
	}

	function selectPlayer(playerId: string) {
		playerStore.selectPlayer(playerId);
		isOpen = false;
	}

	function handleClickOutside(event: MouseEvent) {
		const target = event.target as HTMLElement;
		if (!target.closest('.player-selector')) {
			isOpen = false;
		}
	}
</script>

<svelte:window onclick={handleClickOutside} />

<div class="player-selector relative">
	<button
		class="flex items-center gap-3 px-4 py-2 rounded-lg bg-surface-0 hover:bg-surface-1 transition-colors w-full"
		onclick={toggleDropdown}
		aria-expanded={isOpen}
		aria-haspopup="listbox"
	>
		<Speaker size={20} class="text-accent shrink-0" />

		<div class="flex-1 text-left min-w-0">
			{#if playerStore.selectedPlayer}
				<div class="flex items-center gap-2">
					<span class="text-text font-medium truncate">
						{playerStore.selectedPlayer.name}
					</span>
					{#if playerStore.selectedPlayer.connected}
						<Wifi size={14} class="text-success shrink-0" />
					{:else}
						<WifiOff size={14} class="text-error shrink-0" />
					{/if}
				</div>
				<p class="text-xs text-overlay-1 truncate">
					{playerStore.selectedPlayer.model}
				</p>
			{:else if playerStore.players.length === 0}
				<span class="text-overlay-0">No players found</span>
			{:else}
				<span class="text-overlay-0">Select a player</span>
			{/if}
		</div>

		<ChevronDown
			size={18}
			class="text-overlay-1 shrink-0 transition-transform duration-200 {isOpen ? 'rotate-180' : ''}"
		/>
	</button>

	<!-- Dropdown -->
	{#if isOpen && playerStore.players.length > 0}
		<div
			class="absolute top-full left-0 right-0 mt-2 py-2 bg-surface-0 rounded-lg shadow-xl border border-border z-50 max-h-64 overflow-y-auto"
			role="listbox"
		>
			{#each playerStore.players as player}
				<button
					class="w-full flex items-center gap-3 px-4 py-3 hover:bg-surface-1 transition-colors text-left
						   {player.id === playerStore.selectedPlayerId ? 'bg-surface-1' : ''}"
					onclick={() => selectPlayer(player.id)}
					role="option"
					aria-selected={player.id === playerStore.selectedPlayerId}
				>
					<Speaker
						size={18}
						class={player.id === playerStore.selectedPlayerId ? 'text-accent' : 'text-overlay-1'}
					/>

					<div class="flex-1 min-w-0">
						<div class="flex items-center gap-2">
							<span class="text-text truncate">{player.name}</span>
							{#if player.connected}
								<Wifi size={12} class="text-success shrink-0" />
							{:else}
								<WifiOff size={12} class="text-error shrink-0" />
							{/if}
						</div>
						<p class="text-xs text-overlay-1 truncate">{player.model}</p>
					</div>

					{#if player.isPlaying}
						<div class="flex gap-0.5 items-end h-4">
							<div class="w-1 bg-accent rounded-full animate-bounce" style="height: 60%; animation-delay: 0ms"></div>
							<div class="w-1 bg-accent rounded-full animate-bounce" style="height: 100%; animation-delay: 150ms"></div>
							<div class="w-1 bg-accent rounded-full animate-bounce" style="height: 40%; animation-delay: 300ms"></div>
						</div>
					{/if}
				</button>
			{/each}
		</div>
	{/if}

	<!-- Empty state -->
	{#if isOpen && playerStore.players.length === 0}
		<div
			class="absolute top-full left-0 right-0 mt-2 p-4 bg-surface-0 rounded-lg shadow-xl border border-border z-50"
		>
			<p class="text-overlay-1 text-center text-sm">
				No players connected.<br />
				Start Squeezelite to begin.
			</p>
		</div>
	{/if}
</div>
