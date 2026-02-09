<script lang="ts">
	import { api, type ScanStatus } from '$lib/api';
	import { X, FolderPlus, Loader2, Check, AlertCircle, Trash2 } from 'lucide-svelte';

	// Props
	let { isOpen = $bindable(false), onClose = () => {} }: { isOpen: boolean; onClose: () => void } =
		$props();

	// State
	let folderPath = $state('');
	let folders = $state<string[]>([]);
	let isLoading = $state(false);
	let isScanning = $state(false);
	let scanStatus = $state<ScanStatus | null>(null);
	let error = $state<string | null>(null);
	let success = $state<string | null>(null);

	// Scan polling interval
	let scanPollInterval: ReturnType<typeof setInterval> | null = null;

	// Load folders when modal opens
	$effect(() => {
		if (isOpen) {
			loadFolders();
			checkScanStatus();
		} else {
			// Cleanup when modal closes
			if (scanPollInterval) {
				clearInterval(scanPollInterval);
				scanPollInterval = null;
			}
		}
	});

	async function loadFolders() {
		try {
			folders = await api.getMusicFolders();
		} catch (e) {
			console.error('Failed to load folders:', e);
		}
	}

	async function checkScanStatus() {
		try {
			scanStatus = await api.getScanStatus();
			isScanning = scanStatus.scanning;

			if (isScanning && !scanPollInterval) {
				// Start polling for scan status
				scanPollInterval = setInterval(async () => {
					try {
						scanStatus = await api.getScanStatus();
						isScanning = scanStatus.scanning;
						if (!isScanning && scanPollInterval) {
							clearInterval(scanPollInterval);
							scanPollInterval = null;
							success = `Scan complete! Found ${scanStatus.tracks_found} tracks.`;
						}
					} catch (e) {
						console.error('Failed to get scan status:', e);
					}
				}, 1000);
			}
		} catch (e) {
			console.error('Failed to get scan status:', e);
		}
	}

	async function handleAddFolder() {
		if (!folderPath.trim()) {
			error = 'Please enter a folder path';
			return;
		}

		isLoading = true;
		error = null;
		success = null;

		try {
			folders = await api.addMusicFolder(folderPath.trim());
			folderPath = '';
			success = 'Folder added successfully!';

			// Auto-start scan after adding folder
			await handleStartScan();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to add folder';
		} finally {
			isLoading = false;
		}
	}

	async function handleRemoveFolder(path: string) {
		isLoading = true;
		error = null;

		try {
			folders = await api.removeMusicFolder(path);
			success = 'Folder removed.';
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to remove folder';
		} finally {
			isLoading = false;
		}
	}

	async function handleStartScan() {
		if (folders.length === 0) {
			error = 'Add at least one folder before scanning';
			return;
		}

		isLoading = true;
		error = null;

		try {
			const result = await api.startScan();
			isScanning = result.scanning;
			success = result.status === 'started' ? 'Scan started...' : 'Scan already running';

			// Start polling for status
			checkScanStatus();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to start scan';
		} finally {
			isLoading = false;
		}
	}

	function handleClose() {
		isOpen = false;
		onClose();
	}

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			handleClose();
		} else if (event.key === 'Enter' && folderPath.trim()) {
			handleAddFolder();
		}
	}
</script>

{#if isOpen}
	<!-- Backdrop -->
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4"
		onkeydown={handleKeydown}
		onclick={handleClose}
	>
		<!-- Modal -->
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<!-- svelte-ignore a11y_click_events_have_key_events -->
		<div
			class="bg-base rounded-2xl shadow-2xl w-full max-w-lg border border-surface-1 overflow-hidden"
			onclick={(e) => e.stopPropagation()}
		>
			<!-- Header -->
			<div class="flex items-center justify-between px-6 py-4 border-b border-surface-1">
				<div class="flex items-center gap-3">
					<div
						class="w-10 h-10 rounded-xl bg-gradient-to-br from-accent to-accent-hover flex items-center justify-center"
					>
						<FolderPlus size={20} class="text-crust" />
					</div>
					<div>
						<h2 class="text-lg font-semibold text-text">Music Folders</h2>
						<p class="text-sm text-overlay-1">Add folders to scan for music</p>
					</div>
				</div>
				<button
					class="p-2 rounded-lg hover:bg-surface-0 text-overlay-1 hover:text-text transition-colors"
					onclick={handleClose}
					aria-label="Close"
				>
					<X size={20} />
				</button>
			</div>

			<!-- Content -->
			<div class="p-6 space-y-4">
				<!-- Add Folder Input -->
				<div class="flex gap-2">
					<input
						type="text"
						bind:value={folderPath}
						placeholder="Enter folder path (e.g., D:\Music)"
						class="flex-1 px-4 py-3 rounded-xl bg-surface-0 border border-surface-1
                           text-text placeholder-overlay-0 focus:outline-none focus:ring-2
                           focus:ring-accent focus:border-transparent transition-all"
						disabled={isLoading}
					/>
					<button
						class="px-4 py-3 rounded-xl bg-accent hover:bg-accent-hover text-crust
                           font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed
                           flex items-center gap-2"
						onclick={handleAddFolder}
						disabled={isLoading || !folderPath.trim()}
					>
						{#if isLoading}
							<Loader2 size={18} class="animate-spin" />
						{:else}
							<FolderPlus size={18} />
						{/if}
						Add
					</button>
				</div>

				<!-- Error Message -->
				{#if error}
					<div
						class="flex items-center gap-2 px-4 py-3 rounded-xl bg-error/10 border border-error/20 text-error"
					>
						<AlertCircle size={18} />
						<span class="text-sm">{error}</span>
					</div>
				{/if}

				<!-- Success Message -->
				{#if success && !error}
					<div
						class="flex items-center gap-2 px-4 py-3 rounded-xl bg-success/10 border border-success/20 text-success"
					>
						<Check size={18} />
						<span class="text-sm">{success}</span>
					</div>
				{/if}

				<!-- Folder List -->
				<div class="space-y-2">
					<h3 class="text-sm font-medium text-overlay-1">Configured Folders</h3>
					{#if folders.length === 0}
						<div class="px-4 py-8 rounded-xl bg-surface-0 text-center text-overlay-1">
							<p>No folders configured yet</p>
							<p class="text-sm mt-1">Add a folder above to get started</p>
						</div>
					{:else}
						<div class="space-y-2 max-h-48 overflow-y-auto">
							{#each folders as folder}
								<div
									class="flex items-center justify-between px-4 py-3 rounded-xl bg-surface-0 group"
								>
									<span class="text-text truncate flex-1" title={folder}>{folder}</span>
									<button
										class="p-2 rounded-lg opacity-0 group-hover:opacity-100 hover:bg-error/10
                                       text-overlay-1 hover:text-error transition-all"
										onclick={() => handleRemoveFolder(folder)}
										aria-label="Remove folder"
										disabled={isLoading}
									>
										<Trash2 size={16} />
									</button>
								</div>
							{/each}
						</div>
					{/if}
				</div>

				<!-- Scan Status -->
				{#if isScanning && scanStatus}
					<div class="px-4 py-4 rounded-xl bg-surface-0 space-y-3">
						<div class="flex items-center gap-2 text-accent">
							<Loader2 size={18} class="animate-spin" />
							<span class="font-medium">Scanning...</span>
						</div>
						<div class="space-y-2">
							<div class="flex justify-between text-sm">
								<span class="text-overlay-1">Progress</span>
								<span class="text-text">{Math.round(scanStatus.progress * 100)}%</span>
							</div>
							<div class="w-full h-2 bg-surface-1 rounded-full overflow-hidden">
								<div
									class="h-full bg-gradient-to-r from-accent to-accent-hover transition-all duration-300"
									style="width: {scanStatus.progress * 100}%"
								></div>
							</div>
							<div class="flex justify-between text-sm">
								<span class="text-overlay-1">Tracks found</span>
								<span class="text-text">{scanStatus.tracks_found}</span>
							</div>
							{#if scanStatus.current_folder}
								<div class="text-xs text-overlay-0 truncate">
									{scanStatus.current_folder}
								</div>
							{/if}
						</div>
					</div>
				{/if}
			</div>

			<!-- Footer -->
			<div class="flex items-center justify-end gap-3 px-6 py-4 border-t border-surface-1 bg-mantle">
				<button
					class="px-4 py-2 rounded-xl hover:bg-surface-0 text-overlay-1 hover:text-text
                       font-medium transition-colors"
					onclick={handleClose}
				>
					Close
				</button>
				{#if folders.length > 0 && !isScanning}
					<button
						class="px-4 py-2 rounded-xl bg-accent hover:bg-accent-hover text-crust
                           font-medium transition-colors disabled:opacity-50 flex items-center gap-2"
						onclick={handleStartScan}
						disabled={isLoading}
					>
						{#if isLoading}
							<Loader2 size={16} class="animate-spin" />
						{/if}
						Rescan Library
					</button>
				{/if}
			</div>
		</div>
	</div>
{/if}
