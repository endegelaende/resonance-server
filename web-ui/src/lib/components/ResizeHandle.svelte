<script lang="ts">
  import { onMount } from "svelte";

  // Props
  let {
    position = "left",
    minSize = 150,
    maxSize = 600,
    defaultSize = 250,
    storageKey = "",
    onResize = (_size: number) => {},
    onCollapse = () => {},
  }: {
    position?: "left" | "right";
    minSize?: number;
    maxSize?: number;
    defaultSize?: number;
    storageKey?: string;
    onResize?: (size: number) => void;
    onCollapse?: () => void;
  } = $props();

  // State
  let isDragging = $state(false);
  let startX = $state(0);
  let startSize = $state(0);
  let currentSize = $state(0);
  let lastClickTime = $state(0);

  // Initialize with defaultSize
  $effect(() => {
    if (currentSize === 0) {
      currentSize = defaultSize;
      startSize = defaultSize;
    }
  });

  // Load saved size from localStorage
  onMount(() => {
    if (storageKey) {
      const saved = localStorage.getItem(storageKey);
      if (saved) {
        const size = parseInt(saved, 10);
        if (!isNaN(size) && size >= minSize && size <= maxSize) {
          currentSize = size;
          onResize(size);
        }
      }
    } else {
      // No storage key, just use default
      onResize(currentSize);
    }
  });

  // Save size to localStorage
  function saveSize(size: number) {
    if (storageKey) {
      localStorage.setItem(storageKey, size.toString());
    }
  }

  // Mouse event handlers
  function handleMouseDown(event: MouseEvent) {
    // Check for double-click (collapse toggle)
    const now = Date.now();
    if (now - lastClickTime < 300) {
      onCollapse();
      lastClickTime = 0;
      return;
    }
    lastClickTime = now;

    // Start dragging
    isDragging = true;
    startX = event.clientX;
    startSize = currentSize;

    // Prevent text selection
    event.preventDefault();

    // Add global listeners
    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }

  function handleMouseMove(event: MouseEvent) {
    if (!isDragging) return;

    const delta =
      position === "left" ? event.clientX - startX : startX - event.clientX;

    let newSize = startSize + delta;

    // Clamp to min/max
    newSize = Math.max(minSize, Math.min(maxSize, newSize));

    currentSize = newSize;
    onResize(newSize);
  }

  function handleMouseUp() {
    if (!isDragging) return;

    isDragging = false;
    saveSize(currentSize);

    // Remove global listeners
    document.removeEventListener("mousemove", handleMouseMove);
    document.removeEventListener("mouseup", handleMouseUp);
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  }

  // Touch event handlers for mobile
  function handleTouchStart(event: TouchEvent) {
    const touch = event.touches[0];
    isDragging = true;
    startX = touch.clientX;
    startSize = currentSize;

    document.addEventListener("touchmove", handleTouchMove, { passive: false });
    document.addEventListener("touchend", handleTouchEnd);
  }

  function handleTouchMove(event: TouchEvent) {
    if (!isDragging) return;
    event.preventDefault();

    const touch = event.touches[0];
    const delta =
      position === "left" ? touch.clientX - startX : startX - touch.clientX;

    let newSize = startSize + delta;
    newSize = Math.max(minSize, Math.min(maxSize, newSize));

    currentSize = newSize;
    onResize(newSize);
  }

  function handleTouchEnd() {
    if (!isDragging) return;

    isDragging = false;
    saveSize(currentSize);

    document.removeEventListener("touchmove", handleTouchMove);
    document.removeEventListener("touchend", handleTouchEnd);
  }

  function handleKeyDown(e: KeyboardEvent) {
    if (e.key === "ArrowLeft") {
      const newSize = Math.max(minSize, currentSize - 10);
      currentSize = newSize;
      onResize(newSize);
      saveSize(newSize);
    } else if (e.key === "ArrowRight") {
      const newSize = Math.min(maxSize, currentSize + 10);
      currentSize = newSize;
      onResize(newSize);
      saveSize(newSize);
    } else if (e.key === "Enter" || e.key === " ") {
      onCollapse();
    }
  }
</script>

<!-- svelte-ignore a11y_no_static_element_interactions a11y_no_noninteractive_tabindex a11y_no_noninteractive_element_interactions -->
<div
  class="resize-handle"
  class:dragging={isDragging}
  class:left={position === "left"}
  class:right={position === "right"}
  role="separator"
  aria-orientation="vertical"
  aria-valuenow={currentSize}
  aria-valuemin={minSize}
  aria-valuemax={maxSize}
  aria-label="Resize panel"
  tabindex="0"
  onmousedown={handleMouseDown}
  ontouchstart={handleTouchStart}
  onkeydown={handleKeyDown}
>
  <div class="handle-line"></div>
</div>

<style>
  .resize-handle {
    position: relative;
    width: 12px;
    height: 100%;
    min-height: 100vh;
    cursor: col-resize;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    z-index: 10;
    touch-action: none;
    background: transparent;
  }

  .resize-handle:hover .handle-line,
  .resize-handle.dragging .handle-line {
    background-color: var(--color-accent, #cba6f7);
    width: 4px;
    opacity: 1;
  }

  .resize-handle:hover,
  .resize-handle.dragging {
    background: linear-gradient(
      90deg,
      transparent 0%,
      rgba(203, 166, 247, 0.1) 50%,
      transparent 100%
    );
  }

  .resize-handle:focus {
    outline: none;
  }

  .resize-handle:focus-visible .handle-line {
    background-color: var(--color-accent, #cba6f7);
    width: 3px;
    box-shadow: 0 0 0 2px var(--color-accent, #cba6f7);
  }

  .handle-line {
    width: 2px;
    height: 100%;
    min-height: 100%;
    background-color: var(--color-overlay-0, #6c7086);
    opacity: 0.6;
    transition:
      background-color 0.15s ease,
      width 0.15s ease,
      opacity 0.15s ease;
    border-radius: 2px;
  }

  .resize-handle.dragging .handle-line {
    background-color: var(--color-accent, #cba6f7);
    opacity: 1;
  }

  /* Hover area extends beyond visible line */
  .resize-handle::before {
    content: "";
    position: absolute;
    top: 0;
    bottom: 0;
    left: -4px;
    right: -4px;
  }
</style>
