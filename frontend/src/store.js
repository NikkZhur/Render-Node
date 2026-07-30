import { create } from "zustand";

export const useUiStore = create((set) => ({
  versionPanelOpen: false,
  selectedJobId: null,
  setVersionPanelOpen: (versionPanelOpen) => set({ versionPanelOpen }),
  setSelectedJobId: (selectedJobId) => set({ selectedJobId }),
}));
