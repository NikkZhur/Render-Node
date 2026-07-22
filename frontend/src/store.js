import { create } from "zustand";

export const useUiStore = create((set) => ({
  versionPanelOpen: false,
  selectedJobId: "job-live",
  setVersionPanelOpen: (versionPanelOpen) => set({ versionPanelOpen }),
  setSelectedJobId: (selectedJobId) => set({ selectedJobId }),
}));
