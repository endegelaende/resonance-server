<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { playerStore } from '$lib/stores/player.svelte';
	import ToastContainer from '$lib/components/ToastContainer.svelte';

	let { children } = $props();

	onMount(() => {
		playerStore.initialize();

		return () => {
			playerStore.stopPolling();
		};
	});
</script>

<div class="min-h-screen bg-crust flex flex-col">
	{@render children()}
</div>

<!-- Global toast notifications -->
<ToastContainer />
