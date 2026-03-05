import type { Artist, Album } from "$lib/api";

export type View =
  | "artists"
  | "albums"
  | "tracks"
  | "search"
  | "playlists"
  | "radio"
  | "plugins"
  | "settings"
  | `plugin:${string}`;
export type ModalType = "none" | "add-folder";

class UIStore {
  // Navigation State
  currentView = $state<View>("artists");

  // Active plugin page (when currentView is "plugin:...")
  activePluginId = $state<string | null>(null);

  // Selection Context
  selectedArtist = $state<Artist | null>(null);
  selectedAlbum = $state<Album | null>(null);

  // Layout State
  isSidebarOpen = $state(false); // Mobile/Desktop toggle
  activeModal = $state<ModalType>("none");

  // Navigation Actions
  navigateTo(view: View) {
    this.currentView = view;
    this.activePluginId = null;

    // Reset selection context when navigating to root views
    // This ensures we start fresh when clicking main navigation items
    if (
      view === "artists" ||
      view === "albums" ||
      view === "playlists" ||
      view === "radio" ||
      view === "plugins" ||
      view === "settings"
    ) {
      this.selectedArtist = null;
      this.selectedAlbum = null;
    }
  }

  navigateToPlugin(pluginId: string) {
    this.currentView = `plugin:${pluginId}` as View;
    this.activePluginId = pluginId;
    this.selectedArtist = null;
    this.selectedAlbum = null;
  }

  // Drill down navigation helpers
  viewArtist(artist: Artist) {
    this.selectedArtist = artist;
    this.selectedAlbum = null;
    this.currentView = "albums";
  }

  viewAlbum(album: Album) {
    this.selectedAlbum = album;
    this.currentView = "tracks";
  }

  // History/Back navigation helper for breadcrumbs and back buttons
  goBack() {
    if (this.selectedAlbum) {
      this.selectedAlbum = null;
      this.currentView = "albums";
    } else if (this.selectedArtist) {
      this.selectedArtist = null;
      this.currentView = "artists";
    } else if (this.currentView === "search") {
      this.currentView = "artists"; // Default back from search
    }
  }

  // Layout Actions
  toggleSidebar() {
    this.isSidebarOpen = !this.isSidebarOpen;
  }

  setSidebarOpen(isOpen: boolean) {
    this.isSidebarOpen = isOpen;
  }

  // Modal Actions
  openModal(modal: ModalType) {
    this.activeModal = modal;
  }

  closeModal() {
    this.activeModal = "none";
  }
}

export const uiStore = new UIStore();
