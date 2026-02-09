<script lang="ts">
	import { playerStore } from '$lib/stores/player.svelte';
	import { colorStore } from '$lib/stores/color.svelte';
	import {
		Play,
		Pause,
		SkipBack,
		SkipForward,
		Volume2,
		VolumeX,
		Maximize2
	} from 'lucide-svelte';
	import QualityBadge from './QualityBadge.svelte';
	import CoverArt from './CoverArt.svelte';

	// Track cover art changes and extract colors
	$effect(() => {
		const coverArt = playerStore.currentTrack?.coverArt;
		colorStore.setFromImage(coverArt);
	});

	// Format seconds to mm:ss
	function formatTime(seconds: number): string {
		if (!seconds || seconds < 0) return '0:00';
		const mins = Math.floor(seconds / 60);
		const secs = Math.floor(seconds % 60);
		return `${mins}:${secs.toString().padStart(2, '0')}`;
	}

	// Handle progress bar click
	function handleSeek(event: MouseEvent) {
		const target = event.currentTarget as HTMLDivElement;
		const rect = target.getBoundingClientRect();
		const percent = (event.clientX - rect.left) / rect.width;
		const newTime = percent * playerStore.status.duration;
		playerStore.seek(newTime);
	}

	// Volume preview state
	let volumePreview = $state<number | null>(null);
	let showVolumePreview = $state(false);
	let isDraggingVolume = $state(false);
	let previewVolume = $state(0);

	// Handle volume drag start
	function handleVolumeStart() {
		isDraggingVolume = true;
	}

	// Handle volume slider - live update while dragging
	function handleVolumeInput(event: Event) {
		const target = event.target as HTMLInputElement;
		previewVolume = parseInt(target.value);
		volumePreview = previewVolume;
		showVolumePreview = true;
	}

	// Handle volume slider - commit on release
	function handleVolumeChange(event: Event) {
		const target = event.target as HTMLInputElement;
		playerStore.setVolume(parseInt(target.value));
		showVolumePreview = false;
		volumePreview = null;
		isDraggingVolume = false;
	}

	// Get file extension from path for format badge
	function getFormat(path: string | undefined): string {
		if (!path) return '';
		const ext = path.split('.').pop()?.toUpperCase() || '';
		return ext;
	}
</script>

<div class="relative rounded-xl overflow-hidden color-transition">
	<!-- UltraBlur Background Layer -->
	{#if playerStore.currentTrack?.coverArt}
		<div class="absolute inset-0 -z-10">
			<!-- Blurred album art background -->
			<img
				src={playerStore.currentTrack.coverArt}
				alt=""
				class="absolute inset-0 w-full h-full object-cover scale-150 blur-3xl opacity-40"
				aria-hidden="true"
			/>
			<!-- Gradient overlay for readability -->
			<div class="absolute inset-0 bg-gradient-to-t from-base/90 via-base/70 to-base/50"></div>
		</div>
	{:else}
		<!-- Fallback gradient when no artwork -->
		<div class="absolute inset-0 -z-10 bg-gradient-to-br from-surface-0 to-base"></div>
	{/if}

	<!-- Content -->
	<div class="relative glass rounded-xl p-6 flex flex-col gap-6 backdrop-blur-sm bg-base/30 border border-white/5 color-transition">
		<!-- Album Art & Track Info -->
		<div class="flex gap-6 items-center">
			<!-- Album Art with Glow using CoverArt component -->
			<div class="relative shrink-0 group">
				<!-- Glow effect behind album art - uses dynamic accent color -->
				{#if playerStore.currentTrack?.coverArt}
					<div
						class="absolute inset-0 rounded-lg blur-xl opacity-60 group-hover:opacity-80 transition-all duration-500 dynamic-glow"
						style="background-color: var(--dynamic-accent); transform: scale(1.1);"
					></div>
				{/if}

				<!-- Album art container with BlurHash support -->
				<div
					class="relative w-32 h-32 rounded-lg overflow-hidden shadow-2xl ring-1 ring-white/10 color-transition"
					style="box-shadow: 0 25px 50px -12px rgba(var(--dynamic-accent-rgb), 0.25);"
				>
					<CoverArt
						src={playerStore.currentTrack?.coverArt}
						blurhash={playerStore.currentTrack?.blurhash}
						alt="Album art"
						size="full"
						showDisc={true}
						spinning={playerStore.isPlaying}
						hoverScale={true}
					/>
				</div>

				<!-- Fullscreen button overlay -->
				<button
					class="absolute inset-0 flex items-center justify-center bg-black/0 hover:bg-black/40 rounded-lg opacity-0 group-hover:opacity-100 transition-all duration-200"
					aria-label="Fullscreen"
				>
					<Maximize2 size={24} class="text-white drop-shadow-lg" />
				</button>
			</div>

			<!-- Track Info -->
			<div class="flex flex-col gap-1 min-w-0 flex-1">
				{#if playerStore.currentTrack}
					<h2 class="text-xl font-semibold text-text truncate drop-shadow-sm">
						{playerStore.currentTrack.title}
					</h2>
					<p class="text-subtext-0 truncate">
						{playerStore.currentTrack.artist}
					</p>
					<p class="text-overlay-1 text-sm truncate">
						{playerStore.currentTrack.album}
					</p>

					<!-- Quality Badge -->
					{#if playerStore.currentTrack.path}
						<div class="mt-2">
							<QualityBadge
								format={getFormat(playerStore.currentTrack.path)}
								sampleRate={playerStore.currentTrack.sampleRate}
								bitDepth={playerStore.currentTrack.bitDepth}
								bitrate={playerStore.currentTrack.bitrate}
								channels={playerStore.currentTrack.channels}
							/>
						</div>
					{/if}
				{:else}
					<h2 class="text-xl font-semibold text-overlay-0">No track playing</h2>
					<p class="text-overlay-0">Select a track to start</p>
				{/if}
			</div>
		</div>

		<!-- Progress Bar -->
		<div class="flex flex-col gap-2">
			<button
				class="w-full h-2 bg-surface-1/50 rounded-full cursor-pointer overflow-hidden group backdrop-blur-sm"
				onclick={handleSeek}
				aria-label="Seek"
			>
				<!-- Progress fill with dynamic gradient -->
				<div
					class="h-full rounded-full transition-all duration-150 dynamic-progress group-hover:shadow-[0_0_12px_rgba(var(--dynamic-accent-rgb),0.5)]"
					style="width: {playerStore.progress}%"
				></div>
			</button>
			<div class="flex justify-between text-sm text-overlay-1">
				<span class="font-mono text-xs">{formatTime(playerStore.elapsedTime)}</span>
				<span class="font-mono text-xs">{formatTime(playerStore.status.duration)}</span>
			</div>
		</div>

		<!-- Controls -->
		<div class="flex items-center justify-between">
			<!-- Playback Controls -->
			<div class="flex items-center gap-4">
				<button
					class="p-2 rounded-full hover:bg-white/10 text-text transition-all duration-200 hover:scale-105 active:scale-95"
					onclick={() => playerStore.previous()}
					aria-label="Previous"
				>
					<SkipBack size={24} />
				</button>

				<button
					class="p-4 rounded-full text-crust transition-all duration-200 shadow-lg hover:shadow-xl hover:scale-105 active:scale-95 dynamic-btn"
					onclick={() => playerStore.togglePlayPause()}
					aria-label={playerStore.isPlaying ? 'Pause' : 'Play'}
				>
					{#if playerStore.isPlaying}
						<Pause size={28} fill="currentColor" />
					{:else}
						<Play size={28} fill="currentColor" />
					{/if}
				</button>

				<button
					class="p-2 rounded-full hover:bg-white/10 text-text transition-all duration-200 hover:scale-105 active:scale-95"
					onclick={() => playerStore.next()}
					aria-label="Next"
				>
					<SkipForward size={24} />
				</button>
			</div>

			<!-- Volume -->
			<div class="flex items-center gap-3 relative">
				<button
					class="p-2 rounded-full hover:bg-white/10 text-text transition-all duration-200"
					onclick={() => playerStore.toggleMute()}
					aria-label={playerStore.status.muted ? 'Unmute' : 'Mute'}
				>
					{#if playerStore.status.muted || playerStore.status.volume === 0}
						<VolumeX size={20} />
					{:else}
						<Volume2 size={20} />
					{/if}
				</button>

				<div class="relative">
						<input
							type="range"
							min="0"
							max="100"
							value={playerStore.status.volume}
							oninput={handleVolumeInput}
							onchange={handleVolumeChange}
							onmousedown={handleVolumeStart}
							ontouchstart={handleVolumeStart}
							class="w-24 h-2 bg-surface-1/50 rounded-full appearance-none cursor-pointer backdrop-blur-sm color-transition
								   [&::-webkit-slider-thumb]:appearance-none
								   [&::-webkit-slider-thumb]:w-4
								   [&::-webkit-slider-thumb]:h-4
								   [&::-webkit-slider-thumb]:rounded-full
								   [&::-webkit-slider-thumb]:transition-all
								   [&::-webkit-slider-thumb]:shadow-lg
								   [&::-webkit-slider-thumb]:hover:scale-110"
							style="--tw-slider-thumb-bg: var(--dynamic-accent);"
							aria-label="Volume"
						/>
					<span class="text-sm text-overlay-1 w-10 text-right font-mono tabular-nums">
						{String(isDraggingVolume ? previewVolume : playerStore.status.volume).padStart(3, '\u00A0')}
					</span>
				</div>
			</div>
		</div>
	</div>
</div>
