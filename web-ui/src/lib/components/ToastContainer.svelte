<script lang="ts">
	import { toastStore, type ToastType } from '$lib/stores/toast.svelte';
	import { CheckCircle, XCircle, Info, AlertTriangle, X } from 'lucide-svelte';

	// Icon and color mapping per toast type
	const typeConfig: Record<ToastType, { icon: typeof CheckCircle; colorClass: string; bgClass: string; borderClass: string }> = {
		success: {
			icon: CheckCircle,
			colorClass: 'text-success',
			bgClass: 'bg-success/10',
			borderClass: 'border-success/30',
		},
		error: {
			icon: XCircle,
			colorClass: 'text-error',
			bgClass: 'bg-error/10',
			borderClass: 'border-error/30',
		},
		info: {
			icon: Info,
			colorClass: 'text-accent',
			bgClass: 'bg-accent/10',
			borderClass: 'border-accent/30',
		},
		warning: {
			icon: AlertTriangle,
			colorClass: 'text-warning',
			bgClass: 'bg-warning/10',
			borderClass: 'border-warning/30',
		},
	};
</script>

{#if toastStore.toasts.length > 0}
	<div
		class="fixed bottom-6 right-6 z-[100] flex flex-col-reverse gap-3 pointer-events-none max-w-sm w-full"
		aria-live="polite"
		aria-label="Notifications"
	>
		{#each toastStore.toasts as toast (toast.id)}
			{@const config = typeConfig[toast.type]}
			<div
				class="pointer-events-auto flex items-start gap-3 px-4 py-3 rounded-xl border shadow-2xl backdrop-blur-md transition-all
					   {config.bgClass} {config.borderClass}
					   {toast.dismissing ? 'toast-exit' : 'toast-enter'}"
				role="alert"
			>
				<!-- Icon -->
				<div class="shrink-0 mt-0.5 {config.colorClass}">
					<config.icon size={20} />
				</div>

				<!-- Content -->
				<div class="flex-1 min-w-0">
					<p class="text-sm font-medium text-text leading-snug">
						{toast.message}
					</p>
					{#if toast.detail}
						<p class="text-xs text-overlay-1 mt-1 leading-relaxed">
							{toast.detail}
						</p>
					{/if}
				</div>

				<!-- Dismiss button -->
				<button
					class="shrink-0 p-1 rounded-lg text-overlay-1 hover:text-text hover:bg-white/10 transition-colors -mr-1 -mt-0.5"
					onclick={() => toastStore.dismiss(toast.id)}
					aria-label="Dismiss notification"
				>
					<X size={16} />
				</button>
			</div>
		{/each}
	</div>
{/if}

<style>
	/* Enter animation */
	.toast-enter {
		animation: toast-slide-in 300ms cubic-bezier(0.16, 1, 0.3, 1) forwards;
	}

	/* Exit animation */
	.toast-exit {
		animation: toast-slide-out 300ms cubic-bezier(0.4, 0, 1, 1) forwards;
	}

	@keyframes toast-slide-in {
		from {
			opacity: 0;
			transform: translateX(100%) scale(0.95);
		}
		to {
			opacity: 1;
			transform: translateX(0) scale(1);
		}
	}

	@keyframes toast-slide-out {
		from {
			opacity: 1;
			transform: translateX(0) scale(1);
		}
		to {
			opacity: 0;
			transform: translateX(100%) scale(0.95);
		}
	}
</style>
