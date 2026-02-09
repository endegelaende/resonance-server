import type { Artist, Album } from "$lib/api";

export type View =
  | "artists"
  | "albums"
  | "tracks"
  | "search"
  | "playlists"
  | "settings";
export type ModalType = "none" | "add-folder";

class UIStore {
  // Navigation State
  currentView = $state<View>("artists");

  // Selection Context
  selectedArtist = $state<Artist | null>(null);
  selectedAlbum = $state<Album | null>(null);

  // Layout State
  isSidebarOpen = $state(false); // Mobile/Desktop toggle
  activeModal = $state<ModalType>("none");

  // Navigation Actions
  navigateTo(view: View) {
    this.currentView = view;

    // Reset selection context when navigating to root views
    // This ensures we start fresh when clicking main navigation items
    if (
      view === "artists" ||
      view === "albums" ||
      view === "playlists" ||
      view === "settings"
    ) {
      this.selectedArtist = null;
      this.selectedAlbum = null;
    }
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
