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
		class="flex items-center gap-2 px-3 py-1.5 rounded-lg hover:bg-surface-0/60 transition-colors w-full"
		onclick={toggleDropdown}
		aria-expanded={isOpen}
		aria-haspopup="listbox"
	>
		<Speaker size={14} class="text-overlay-1 shrink-0" />

		<div class="flex-1 text-left min-w-0">
			{#if playerStore.selectedPlayer}
				<div class="flex items-center gap-1.5">
					<span class="text-sm text-text truncate">
						{playerStore.selectedPlayer.name}
					</span>
					{#if playerStore.selectedPlayer.connected}
						<Wifi size={10} class="text-success/70 shrink-0" />
					{:else}
						<WifiOff size={10} class="text-error/70 shrink-0" />
					{/if}
				</div>
				<p class="text-[10px] text-overlay-0 truncate">
					{playerStore.selectedPlayer.model}
				</p>
			{:else if playerStore.players.length === 0}
				<span class="text-xs text-overlay-0">No players</span>
			{:else}
				<span class="text-xs text-overlay-0">Select player</span>
			{/if}
		</div>

		<ChevronDown
			size={14}
			class="text-overlay-0 shrink-0 transition-transform duration-200 {isOpen ? 'rotate-180' : ''}"
		/>
	</button>

	<!-- Dropdown -->
	{#if isOpen && playerStore.players.length > 0}
		<div
			class="absolute top-full left-0 right-0 mt-1 py-1 bg-mantle rounded-lg shadow-xl border border-border/50 z-50 max-h-56 overflow-y-auto"
			role="listbox"
		>
			{#each playerStore.players as player}
				<button
					class="w-full flex items-center gap-2.5 px-3 py-2 hover:bg-surface-0/60 transition-colors text-left
						   {player.id === playerStore.selectedPlayerId ? 'bg-surface-0/40' : ''}"
					onclick={() => selectPlayer(player.id)}
					role="option"
					aria-selected={player.id === playerStore.selectedPlayerId}
				>
					<Speaker
						size={14}
						class={player.id === playerStore.selectedPlayerId ? 'text-accent' : 'text-overlay-0'}
					/>

					<div class="flex-1 min-w-0">
						<div class="flex items-center gap-1.5">
							<span class="text-sm text-text truncate">{player.name}</span>
							{#if player.connected}
								<Wifi size={10} class="text-success/70 shrink-0" />
							{:else}
								<WifiOff size={10} class="text-error/70 shrink-0" />
							{/if}
						</div>
						<p class="text-[10px] text-overlay-0 truncate">{player.model}</p>
					</div>

					{#if player.isPlaying}
						<div class="flex gap-[2px] items-end h-3">
							<div class="w-[2px] rounded-full animate-bounce" style="height: 55%; animation-delay: 0ms; background-color: var(--dynamic-accent, var(--color-accent));"></div>
							<div class="w-[2px] rounded-full animate-bounce" style="height: 100%; animation-delay: 150ms; background-color: var(--dynamic-accent, var(--color-accent));"></div>
							<div class="w-[2px] rounded-full animate-bounce" style="height: 40%; animation-delay: 300ms; background-color: var(--dynamic-accent, var(--color-accent));"></div>
						</div>
					{/if}
				</button>
			{/each}
		</div>
	{/if}

	<!-- Empty state -->
	{#if isOpen && playerStore.players.length === 0}
		<div
			class="absolute top-full left-0 right-0 mt-1 p-3 bg-mantle rounded-lg shadow-xl border border-border/50 z-50"
		>
			<p class="text-overlay-0 text-center text-xs">
				No players connected.<br />
				Start Squeezelite to begin.
			</p>
		</div>
	{/if}
</div>
