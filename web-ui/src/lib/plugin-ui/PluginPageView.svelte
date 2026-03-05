<script lang="ts">
  import { api } from "$lib/api";
  import { toastStore } from "$lib/stores/toast.svelte";
  import PluginRenderer from "./PluginRenderer.svelte";
  import { setContext, onMount } from "svelte";
  import { Loader2, AlertTriangle, RefreshCw } from "lucide-svelte";

  let { pluginId }: { pluginId: string } = $props();

  // Capture in a local const to avoid Svelte's state_referenced_locally warning.
  // pluginId never changes during this component's lifetime — a new instance is
  // created when the user navigates to a different plugin.
  const currentPluginId = pluginId;

  let page = $state<any>(null);
  let error = $state<string | null>(null);
  let loading = $state(true);
  let useSSE = $state(true);

  setContext("pluginId", currentPluginId);

  async function fetchUI() {
    try {
      page = await api.getPluginUI(currentPluginId);
      error = null;
    } catch (e: any) {
      error = e.message || "Failed to load plugin UI";
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    fetchUI();

    let eventSource: EventSource | null = null;
    let pollInterval: ReturnType<typeof setInterval> | undefined;
    let sseReconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let sseRetryCount = 0;
    const MAX_SSE_RETRIES = 3;

    function startSSE() {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }

      // Same-origin request — no base URL needed
      const url = `/api/plugins/${currentPluginId}/events`;

      try {
        eventSource = new EventSource(url);

        eventSource.onopen = () => {
          sseRetryCount = 0;
          useSSE = true;
          // SSE connected — stop polling if it was running as fallback
          if (pollInterval) {
            clearInterval(pollInterval);
            pollInterval = undefined;
          }
        };

        eventSource.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            if (data.event === "ui_refresh") {
              fetchUI();
            }
          } catch {
            // Ignore malformed SSE data
          }
        };

        eventSource.onerror = () => {
          // SSE connection failed or was lost
          if (eventSource) {
            eventSource.close();
            eventSource = null;
          }

          sseRetryCount++;

          if (sseRetryCount <= MAX_SSE_RETRIES) {
            // Retry SSE with exponential backoff
            const delay = Math.min(1000 * Math.pow(2, sseRetryCount - 1), 10000);
            sseReconnectTimer = setTimeout(startSSE, delay);
          } else {
            // Give up on SSE, fall back to polling
            useSSE = false;
            startPolling();
          }
        };
      } catch {
        // EventSource constructor failed (e.g. unsupported browser)
        useSSE = false;
        startPolling();
      }
    }

    function startPolling() {
      if (pollInterval) return; // Already polling

      // Wait for page to load so we know the refresh_interval
      const checkInterval = setInterval(() => {
        if (page?.refresh_interval && page.refresh_interval > 0 && !pollInterval) {
          pollInterval = setInterval(fetchUI, page.refresh_interval * 1000);
          clearInterval(checkInterval);
        }
      }, 500);

      // Clean up the check interval if component unmounts before page loads
      const origCleanup = cleanup;
      cleanup = () => {
        clearInterval(checkInterval);
        origCleanup();
      };
    }

    let cleanup = () => {};

    // Try SSE first; if the browser doesn't support EventSource, fall back
    if (typeof EventSource !== "undefined") {
      startSSE();
    } else {
      useSSE = false;
      startPolling();
    }

    return () => {
      cleanup();
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      if (pollInterval) {
        clearInterval(pollInterval);
      }
      if (sseReconnectTimer) {
        clearTimeout(sseReconnectTimer);
      }
    };
  });
</script>

<div class="flex-1 overflow-y-auto">
  {#if loading}
    <div class="flex items-center justify-center py-24">
      <Loader2
        size={32}
        class="animate-spin dynamic-accent color-transition"
      />
    </div>
  {:else if error}
    <div class="flex flex-col items-center justify-center py-24 text-overlay-1">
      <AlertTriangle size={48} class="text-error mb-4" />
      <h3 class="text-lg font-medium text-text mb-2">
        Failed to load plugin UI
      </h3>
      <p class="text-sm mb-4">{error}</p>
      <button
        class="px-4 py-2 rounded-lg bg-surface-0 hover:bg-surface-1 text-text transition-colors flex items-center gap-2"
        onclick={() => {
          loading = true;
          fetchUI();
        }}
      >
        <RefreshCw size={16} />
        Retry
      </button>
    </div>
  {:else if page}
    <!-- Page header -->
    <div class="px-6 py-5 border-b border-border">
      <h2 class="text-xl font-semibold text-text">{page.title}</h2>
    </div>

    <!-- Components -->
    <div class="p-6 space-y-4">
      {#each page.components as component}
        <PluginRenderer {component} />
      {/each}
    </div>
  {/if}
</div>
