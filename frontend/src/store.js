import { create } from "zustand";

export const useUiStore = create((set) => ({
  versionPanelOpen: false,
  previewTab: "preview",
  selectedJobId: "job-live",
  setVersionPanelOpen: (versionPanelOpen) => set({ versionPanelOpen }),
  setPreviewTab: (previewTab) => set({ previewTab }),
  setSelectedJobId: (selectedJobId) => set({ selectedJobId }),
}));

