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
</script>

<div class="flex flex-col h-full">
	<!-- Header -->
	<div class="flex items-center justify-between px-4 py-3 border-b border-border">
		<div class="flex items-center gap-2">
			<ListMusic size={20} class="dynamic-accent color-transition" />
			<h2 class="font-semibold text-text">Queue</h2>
			{#if playerStore.playlist.length > 0}
				<span class="text-sm text-overlay-1">
					({playerStore.playlist.length})
				</span>
			{/if}
		</div>

		{#if playerStore.playlist.length > 0}
			<button
				class="p-2 rounded-lg hover:bg-surface-1 text-overlay-1 hover:text-error transition-colors"
				onclick={handleClear}
				aria-label="Clear queue"
				title="Clear queue"
			>
				<Trash2 size={18} />
			</button>
		{/if}
	</div>

	<!-- Queue List -->
	<div class="flex-1 overflow-y-auto">
		{#if playerStore.playlist.length === 0}
			<div class="flex flex-col items-center justify-center h-full text-overlay-1 px-4">
				<ListMusic size={48} class="mb-4 opacity-50" />
				<p class="text-center">Your queue is empty</p>
				<p class="text-sm text-overlay-0 mt-1 text-center">
					Add tracks from the library to start playing
				</p>
			</div>
		{:else}
			<div class="flex flex-col py-2">
				{#each playerStore.playlist as track, index}
					<div
						class="group flex items-center gap-3 px-4 py-2 hover:bg-surface-0 transition-colors cursor-pointer
							   {index === playerStore.status.playlistIndex ? 'bg-surface-0' : ''}
							   {isJumpInFlight ? 'pointer-events-none opacity-70' : ''}"
						onclick={() => handleTrackClick(index)}
						onkeydown={(e) => e.key === 'Enter' && handleTrackClick(index)}
						role="button"
						tabindex="0"
					>
						<!-- Drag Handle -->
						<div class="cursor-grab opacity-0 group-hover:opacity-100 transition-opacity text-overlay-1">
							<GripVertical size={16} />
						</div>

						<!-- Track Number / Now Playing Indicator -->
						<div class="w-6 flex items-center justify-center shrink-0">
							{#if index === playerStore.status.playlistIndex && playerStore.isPlaying}
								<div class="flex gap-0.5 items-end h-4">
									<div class="w-0.5 rounded-full animate-bounce color-transition" style="height: 60%; animation-delay: 0ms; background-color: var(--dynamic-accent);"></div>
									<div class="w-0.5 rounded-full animate-bounce color-transition" style="height: 100%; animation-delay: 150ms; background-color: var(--dynamic-accent);"></div>
									<div class="w-0.5 rounded-full animate-bounce color-transition" style="height: 40%; animation-delay: 300ms; background-color: var(--dynamic-accent);"></div>
								</div>
							{:else if index === playerStore.status.playlistIndex}
								<Play size={14} class="dynamic-accent color-transition" fill="currentColor" />
							{:else}
								<span class="text-sm text-overlay-1">{index + 1}</span>
							{/if}
						</div>

						<!-- Track Info -->
						<div class="flex-1 min-w-0">
							<p class="text-sm truncate color-transition {index === playerStore.status.playlistIndex ? 'dynamic-accent' : 'text-text'}">
								{track.title}
							</p>
							<p class="text-xs text-overlay-1 truncate">
								{track.artist}
							</p>
						</div>

						<!-- Duration -->
						<span class="text-xs text-overlay-1 shrink-0">
							{formatDuration(track.duration)}
						</span>

						<!-- Remove Button -->
						<button
							class="p-1 rounded-full hover:bg-surface-1 text-overlay-1 hover:text-error
								   opacity-0 group-hover:opacity-100 transition-all shrink-0"
							aria-label="Remove from queue"
						>
							<X size={14} />
						</button>
					</div>
				{/each}
			</div>
		{/if}
	</div>

	<!-- Queue Footer Stats -->
	{#if playerStore.playlist.length > 0}
		<div class="px-4 py-3 border-t border-border text-sm text-overlay-1">
			<span>
				{playerStore.playlist.length} tracks
			</span>
			<span class="mx-2">•</span>
			<span>
				{formatDuration(playerStore.playlist.reduce((acc, t) => acc + (t.duration || 0), 0))}
			</span>
		</div>
	{/if}
</div>
