<script lang="ts">
	import { playerStore } from '$lib/stores/player.svelte';
	import { colorStore } from '$lib/stores/color.svelte';
	import { ListMusic, Trash2, GripVertical, Play, X, Loader2 } from 'lucide-svelte';

	// Prevent duplicate clicks from firing multiple jumps
	let isJumpInFlight = $state(false);

	// Format duration to mm:ss
	function formatDuration(seconds: number): string {
		if (!seconds || seconds < 0) return '--:--';
		const mins = Math.floor(seconds / 60);
		const secs = Math.floor(seconds % 60);
		return `${mins}:${secs.toString().padStart(2, '0')}`;
	}

	function handleClear() {
		playerStore.clearPlaylist();
	}

	async function handleTrackClick(index: number) {
		if (isJumpInFlight) return;

		const track = playerStore.playlist[index];
		if (!track) return;

		isJumpInFlight = true;
		try {
			await playerStore.jumpToIndex(index, track);
		} finally {
			isJumpInFlight = false;
		}
	}

	async function handleRemoveTrack(index: number, event: MouseEvent) {
		// Stop propagation so clicking the remove button doesn't trigger track jump
		event.stopPropagation();
		await playerStore.removeTrack(index);
	}
</script>

<div class="flex flex-col h-full">
	<!-- Header -->
	<div class="flex items-center justify-between px-4 py-3">
		<div class="flex items-center gap-2">
			<ListMusic size={16} class="text-overlay-1" />
			<h2 class="text-sm font-medium text-text">Queue</h2>
			{#if playerStore.playlist.length > 0}
				<span class="text-xs text-overlay-0">
					{playerStore.playlist.length}
				</span>
			{/if}
		</div>

		{#if playerStore.playlist.length > 0}
			<button
				class="p-1.5 rounded-md text-overlay-0 hover:text-error transition-colors"
				onclick={handleClear}
				aria-label="Clear queue"
				title="Clear queue"
			>
				<Trash2 size={14} />
			</button>
		{/if}
	</div>

	<!-- Queue List -->
	<div class="flex-1 overflow-y-auto">
		{#if playerStore.playlist.length === 0}
			<div class="flex flex-col items-center justify-center h-full text-overlay-0 px-6">
				<ListMusic size={32} class="mb-3 opacity-30" />
				<p class="text-sm text-center">Your queue is empty</p>
				<p class="text-xs mt-1 text-center opacity-60">
					Add tracks from the library to start playing
				</p>
			</div>
		{:else}
			<div class="flex flex-col py-1">
				{#each playerStore.playlist as track, index}
					<div
						class="group flex items-center gap-2 px-3 py-1.5 hover:bg-surface-0/60 transition-colors cursor-pointer
							   {index === playerStore.status.playlistIndex ? 'bg-surface-0/40' : ''}
							   {isJumpInFlight ? 'pointer-events-none opacity-70' : ''}"
						onclick={() => handleTrackClick(index)}
						onkeydown={(e) => e.key === 'Enter' && handleTrackClick(index)}
						role="button"
						tabindex="0"
					>
						<!-- Track Number / Now Playing Indicator -->
						<div class="w-5 flex items-center justify-center shrink-0">
							{#if index === playerStore.status.playlistIndex && playerStore.isPlaying}
								<div class="flex gap-[2px] items-end h-3">
									<div class="w-[2px] rounded-full animate-bounce" style="height: 55%; animation-delay: 0ms; background-color: var(--dynamic-accent);"></div>
									<div class="w-[2px] rounded-full animate-bounce" style="height: 100%; animation-delay: 150ms; background-color: var(--dynamic-accent);"></div>
									<div class="w-[2px] rounded-full animate-bounce" style="height: 40%; animation-delay: 300ms; background-color: var(--dynamic-accent);"></div>
								</div>
							{:else if index === playerStore.status.playlistIndex}
								<Play size={11} class="dynamic-accent color-transition" fill="currentColor" />
							{:else}
								<span class="text-[10px] text-overlay-0 font-mono">{index + 1}</span>
							{/if}
						</div>

						<!-- Track Info -->
						<div class="flex-1 min-w-0">
							<p class="text-xs truncate color-transition {index === playerStore.status.playlistIndex ? 'dynamic-accent' : 'text-text'}">
								{track.title}
							</p>
							<p class="text-[10px] text-overlay-0 truncate">
								{track.artist}
							</p>
						</div>

						<!-- Duration -->
						<span class="text-[10px] text-overlay-0 shrink-0 font-mono tabular-nums">
							{formatDuration(track.duration)}
						</span>

						<!-- Remove Button -->
						<button
							class="p-0.5 rounded text-overlay-0 hover:text-error
								   opacity-0 group-hover:opacity-100 transition-all shrink-0"
							onclick={(e) => handleRemoveTrack(index, e)}
							aria-label="Remove from queue"
							title="Remove from queue"
						>
							<X size={12} />
						</button>
					</div>
				{/each}
			</div>
		{/if}
	</div>

	<!-- Queue Footer Stats -->
	{#if playerStore.playlist.length > 0}
		<div class="px-4 py-2 text-[10px] text-overlay-0 font-mono tabular-nums">
			{playerStore.playlist.length} tracks · {formatDuration(playerStore.playlist.reduce((acc, t) => acc + (t.duration || 0), 0))}
		</div>
	{/if}
</div>
