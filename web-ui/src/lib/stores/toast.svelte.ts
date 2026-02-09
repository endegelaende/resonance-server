/**
 * Toast Notification Store
 *
 * Provides a simple, reactive toast notification system.
 * Toasts auto-dismiss after a configurable duration and support
 * success, error, info, and warning variants.
 *
 * Uses Svelte 5 Runes for reactive state management.
 */

// =============================================================================
// Types
// =============================================================================

export type ToastType = "success" | "error" | "info" | "warning";

export interface Toast {
  /** Unique identifier */
  id: number;
  /** Toast variant */
  type: ToastType;
  /** Main message text */
  message: string;
  /** Optional detail/description text */
  detail?: string;
  /** Duration in ms before auto-dismiss (0 = manual dismiss only) */
  duration: number;
  /** Timestamp when the toast was created */
  createdAt: number;
  /** Whether the toast is currently dismissing (for exit animation) */
  dismissing: boolean;
}

interface ToastOptions {
  /** Optional detail/description text shown below the message */
  detail?: string;
  /** Duration in ms before auto-dismiss. Defaults vary by type. */
  duration?: number;
}

// =============================================================================
// Constants
// =============================================================================

const DEFAULT_DURATIONS: Record<ToastType, number> = {
  success: 3500,
  info: 4000,
  warning: 5000,
  error: 6000,
};

/** Maximum number of visible toasts at once */
const MAX_VISIBLE = 5;

/** Duration of the exit animation in ms */
const DISMISS_ANIMATION_MS = 300;

// =============================================================================
// State
// =============================================================================

let toasts = $state<Toast[]>([]);
let nextId = 0;

// Timer map for auto-dismiss (id -> timeout handle)
const timers = new Map<number, ReturnType<typeof setTimeout>>();

// =============================================================================
// Internal Helpers
// =============================================================================

function addToast(type: ToastType, message: string, options?: ToastOptions): number {
  const id = nextId++;
  const duration = options?.duration ?? DEFAULT_DURATIONS[type];

  const toast: Toast = {
    id,
    type,
    message,
    detail: options?.detail,
    duration,
    createdAt: Date.now(),
    dismissing: false,
  };

  // Prepend (newest first) and cap the list
  toasts = [toast, ...toasts].slice(0, MAX_VISIBLE);

  // Schedule auto-dismiss if duration > 0
  if (duration > 0) {
    const timer = setTimeout(() => {
      dismiss(id);
    }, duration);
    timers.set(id, timer);
  }

  return id;
}

function dismiss(id: number): void {
  // Clear any pending auto-dismiss timer
  const timer = timers.get(id);
  if (timer) {
    clearTimeout(timer);
    timers.delete(id);
  }

  // Mark as dismissing (triggers exit animation)
  toasts = toasts.map((t) =>
    t.id === id ? { ...t, dismissing: true } : t,
  );

  // Remove from list after animation completes
  setTimeout(() => {
    toasts = toasts.filter((t) => t.id !== id);
  }, DISMISS_ANIMATION_MS);
}

function dismissAll(): void {
  // Clear all timers
  for (const timer of timers.values()) {
    clearTimeout(timer);
  }
  timers.clear();

  // Mark all as dismissing
  toasts = toasts.map((t) => ({ ...t, dismissing: true }));

  // Remove all after animation
  setTimeout(() => {
    toasts = [];
  }, DISMISS_ANIMATION_MS);
}

// =============================================================================
// Public API
// =============================================================================

/**
 * Show a success toast (e.g. "Album deleted", "Folder added")
 */
function success(message: string, options?: ToastOptions): number {
  return addToast("success", message, options);
}

/**
 * Show an error toast (replaces alert() calls)
 */
function error(message: string, options?: ToastOptions): number {
  return addToast("error", message, options);
}

/**
 * Show an info toast (e.g. "Scan started", "Loading...")
 */
function info(message: string, options?: ToastOptions): number {
  return addToast("info", message, options);
}

/**
 * Show a warning toast
 */
function warning(message: string, options?: ToastOptions): number {
  return addToast("warning", message, options);
}

// =============================================================================
// Export
// =============================================================================

export const toastStore = {
  /** Current list of active toasts (reactive) */
  get toasts() {
    return toasts;
  },

  /** Duration of dismiss animation (for component use) */
  get DISMISS_ANIMATION_MS() {
    return DISMISS_ANIMATION_MS;
  },

  // Actions
  success,
  error,
  info,
  warning,
  dismiss,
  dismissAll,
};
