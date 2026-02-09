/**
 * Color Store - Dynamic Accent Colors with node-vibrant
 *
 * Extracts color palettes from album artwork to create an immersive,
 * adaptive UI experience. Palettes are cached to avoid re-extraction.
 *
 * Uses Svelte 5 Runes for reactive state management.
 */

import { Vibrant } from "node-vibrant/browser";

// =============================================================================
// Types
// =============================================================================

export interface ColorPalette {
  /** Primary vibrant color - used for accents, buttons */
  vibrant: string;
  /** Lighter vibrant - used for hover states, highlights */
  lightVibrant: string;
  /** Darker vibrant - used for shadows, dark accents */
  darkVibrant: string;
  /** Muted color - used for backgrounds, subtle elements */
  muted: string;
  /** Light muted - used for secondary backgrounds */
  lightMuted: string;
  /** Dark muted - used for text on light backgrounds */
  darkMuted: string;
}

// Default palette (Catppuccin Mauve-based) when no artwork
const DEFAULT_PALETTE: ColorPalette = {
  vibrant: "#cba6f7", // Mauve
  lightVibrant: "#f5c2e7", // Pink
  darkVibrant: "#89b4fa", // Blue
  muted: "#6c7086", // Overlay0
  lightMuted: "#9399b2", // Overlay2
  darkMuted: "#45475a", // Surface1
};

// =============================================================================
// State
// =============================================================================

// Cache for extracted palettes (URL -> Palette)
const paletteCache = new Map<string, ColorPalette>();

// Current active palette
let currentPalette = $state<ColorPalette>(DEFAULT_PALETTE);

// Current image URL being processed
let currentImageUrl = $state<string | null>(null);

// Loading state
let isExtracting = $state(false);

// Error state
let extractionError = $state<string | null>(null);

// =============================================================================
// Helpers
// =============================================================================

/**
 * Convert RGB array to hex color string
 */
function rgbToHex(rgb: [number, number, number]): string {
  return (
    "#" +
    rgb
      .map((c) => {
        const hex = Math.round(c).toString(16);
        return hex.length === 1 ? "0" + hex : hex;
      })
      .join("")
  );
}

/**
 * Check if a color is too dark (for readability)
 */
function isTooDark(hex: string): boolean {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  // Luminance formula
  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return luminance < 0.2;
}

/**
 * Check if a color is too light (for readability)
 */
function isTooLight(hex: string): boolean {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return luminance > 0.85;
}

/**
 * Adjust color saturation to ensure vibrancy
 */
function ensureVibrant(hex: string): string {
  // Parse hex to RGB
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);

  // Convert to HSL
  const rNorm = r / 255;
  const gNorm = g / 255;
  const bNorm = b / 255;

  const max = Math.max(rNorm, gNorm, bNorm);
  const min = Math.min(rNorm, gNorm, bNorm);
  const l = (max + min) / 2;

  let h = 0;
  let s = 0;

  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);

    switch (max) {
      case rNorm:
        h = ((gNorm - bNorm) / d + (gNorm < bNorm ? 6 : 0)) / 6;
        break;
      case gNorm:
        h = ((bNorm - rNorm) / d + 2) / 6;
        break;
      case bNorm:
        h = ((rNorm - gNorm) / d + 4) / 6;
        break;
    }
  }

  // Boost saturation if too low
  const minSaturation = 0.4;
  if (s < minSaturation) {
    s = minSaturation;
  }

  // Convert back to RGB
  const hslToRgb = (
    h: number,
    s: number,
    l: number,
  ): [number, number, number] => {
    let r: number, g: number, b: number;

    if (s === 0) {
      r = g = b = l;
    } else {
      const hue2rgb = (p: number, q: number, t: number) => {
        if (t < 0) t += 1;
        if (t > 1) t -= 1;
        if (t < 1 / 6) return p + (q - p) * 6 * t;
        if (t < 1 / 2) return q;
        if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
        return p;
      };

      const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
      const p = 2 * l - q;

      r = hue2rgb(p, q, h + 1 / 3);
      g = hue2rgb(p, q, h);
      b = hue2rgb(p, q, h - 1 / 3);
    }

    return [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255)];
  };

  return rgbToHex(hslToRgb(h, s, l));
}

// =============================================================================
// Core Functions
// =============================================================================

/**
 * Extract color palette from an image URL
 */
async function extractPalette(imageUrl: string): Promise<ColorPalette> {
  // Check cache first
  if (paletteCache.has(imageUrl)) {
    return paletteCache.get(imageUrl)!;
  }

  try {
    const palette = await Vibrant.from(imageUrl)
      .quality(5) // 1-10, lower = more accurate but slower
      .build()
      .getPalette();

    // Extract colors with fallbacks
    const vibrant = palette.Vibrant?.hex || DEFAULT_PALETTE.vibrant;
    const lightVibrant =
      palette.LightVibrant?.hex || DEFAULT_PALETTE.lightVibrant;
    const darkVibrant = palette.DarkVibrant?.hex || DEFAULT_PALETTE.darkVibrant;
    const muted = palette.Muted?.hex || DEFAULT_PALETTE.muted;
    const lightMuted = palette.LightMuted?.hex || DEFAULT_PALETTE.lightMuted;
    const darkMuted = palette.DarkMuted?.hex || DEFAULT_PALETTE.darkMuted;

    // Build result with quality adjustments
    const result: ColorPalette = {
      vibrant: isTooDark(vibrant) ? ensureVibrant(lightVibrant) : vibrant,
      lightVibrant: isTooLight(lightVibrant) ? vibrant : lightVibrant,
      darkVibrant: darkVibrant,
      muted: muted,
      lightMuted: lightMuted,
      darkMuted: darkMuted,
    };

    // Cache the result
    paletteCache.set(imageUrl, result);

    return result;
  } catch (error) {
    console.warn("Failed to extract palette:", error);
    return DEFAULT_PALETTE;
  }
}

/**
 * Apply palette to CSS custom properties
 */
function applyPaletteToCss(palette: ColorPalette): void {
  const root = document.documentElement;

  // Set CSS custom properties for dynamic theming
  root.style.setProperty("--dynamic-accent", palette.vibrant);
  root.style.setProperty("--dynamic-accent-light", palette.lightVibrant);
  root.style.setProperty("--dynamic-accent-dark", palette.darkVibrant);
  root.style.setProperty("--dynamic-muted", palette.muted);
  root.style.setProperty("--dynamic-muted-light", palette.lightMuted);
  root.style.setProperty("--dynamic-muted-dark", palette.darkMuted);

  // Also set RGB values for use with alpha
  const hexToRgbValues = (hex: string) => {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `${r}, ${g}, ${b}`;
  };

  root.style.setProperty(
    "--dynamic-accent-rgb",
    hexToRgbValues(palette.vibrant),
  );
  root.style.setProperty(
    "--dynamic-accent-light-rgb",
    hexToRgbValues(palette.lightVibrant),
  );
  root.style.setProperty(
    "--dynamic-accent-dark-rgb",
    hexToRgbValues(palette.darkVibrant),
  );
}

// =============================================================================
// Actions
// =============================================================================

/**
 * Set colors from an image URL (extracts palette and applies it)
 */
async function setFromImage(
  imageUrl: string | null | undefined,
): Promise<void> {
  // Skip if same image or no image
  if (!imageUrl) {
    resetToDefault();
    return;
  }

  if (imageUrl === currentImageUrl) {
    return;
  }

  currentImageUrl = imageUrl;
  isExtracting = true;
  extractionError = null;

  try {
    const palette = await extractPalette(imageUrl);
    currentPalette = palette;
    applyPaletteToCss(palette);
  } catch (error) {
    extractionError = error instanceof Error ? error.message : "Unknown error";
    console.error("Color extraction failed:", error);
    // Keep current palette on error
  } finally {
    isExtracting = false;
  }
}

/**
 * Reset to default palette
 */
function resetToDefault(): void {
  currentPalette = DEFAULT_PALETTE;
  currentImageUrl = null;
  applyPaletteToCss(DEFAULT_PALETTE);
}

/**
 * Clear the palette cache
 */
function clearCache(): void {
  paletteCache.clear();
}

/**
 * Get cache size (for debugging)
 */
function getCacheSize(): number {
  return paletteCache.size;
}

// =============================================================================
// Initialize
// =============================================================================

/**
 * Initialize color store (applies default CSS variables)
 */
function initialize(): void {
  applyPaletteToCss(DEFAULT_PALETTE);
}

// =============================================================================
// Export
// =============================================================================

export const colorStore = {
  // State (getters)
  get palette() {
    return currentPalette;
  },
  get isExtracting() {
    return isExtracting;
  },
  get error() {
    return extractionError;
  },
  get currentImageUrl() {
    return currentImageUrl;
  },

  // Derived colors (convenience getters)
  get vibrant() {
    return currentPalette.vibrant;
  },
  get lightVibrant() {
    return currentPalette.lightVibrant;
  },
  get darkVibrant() {
    return currentPalette.darkVibrant;
  },
  get muted() {
    return currentPalette.muted;
  },
  get lightMuted() {
    return currentPalette.lightMuted;
  },
  get darkMuted() {
    return currentPalette.darkMuted;
  },

  // Actions
  setFromImage,
  resetToDefault,
  clearCache,
  getCacheSize,
  initialize,

  // Constants
  DEFAULT_PALETTE,
};
