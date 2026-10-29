import { useEffect, useEffectEvent, useId, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createPairingUrl } from "./accessBootstrap";
import { accessApi, artifactsApi, blenderApi, devicesApi, jobsApi, systemApi } from "./api";
import { FRAMES_PER_PAGE, paddedFrame } from "./artifactPresentation";
import { appendMetricSample, metricChartSegments, metricPercent, metricSeries, resourceKey } from "./metricHistory";
import { getLogLevel } from "./logPresentation";
import {
  ALL_GPUS_PER_FRAME,
  FRAME_PER_GPU,
  normalizeGpuRenderMode,
  supportsFramePerGpu,
} from "./gpuRenderMode";
import { buildProgressRows } from "./renderProgress";
import {
  EtaSmoother,
  formatDuration,
  renderDurationSeconds,
  renderTimingPresentation,
} from "./renderTiming";
import { useRenderEvents } from "./realtime";
import { ENGINE_API_VALUES, applyJobConfiguration } from "./jobPresentation";
import { useUiStore } from "./store";
import Artifacts from "./features/artifacts/Artifacts";
import VersionPanel from "./features/blender/VersionPanel";
import { Icon, SectionHeading } from "./ui/components";
import { useDialogBehavior, useModalEntrance } from "./ui/dialogHooks";

const LOG_TAB_TRANSITION_FALLBACK_MS = 180;
const LOG_PANEL_TRANSITION_FALLBACK_MS = 280;
const COMPUTE_DEVICES = ["OptiX", "CUDA", "CPU"];
const FRAME_MODES = ["single", "range", "all"];
const RENDER_ENGINES = ["Cycles", "Eevee", "Workbench"];
const JOBS_PER_PAGE = 10;
const draftJob = {
  id: "draft",
  shortId: "NEW",
  name: "New render",
  file: "Choose a .blend or ZIP",
  status: "created",
  progress: 0,
  frame: "1–240",
  frame_mode: "RANGE",
  frame_start: 1,
  frame_end: 240,
  gpu_ids: [],
  gpu_render_mode: FRAME_PER_GPU,
  engine: "Cycles",
  device: "OptiX",
  version: "5.2.0",
  created: "Not uploaded",
};

function compactLogEntries(lines) {
  return lines.reduce((entries, [time, line]) => {
    const previous = entries.at(-1);
    if (previous?.line === line) {
      if (line.trim()) previous.count += 1;
      previous.time = time || previous.time;
      return entries;
    }
    entries.push({ count: 1, level: getLogLevel(line), line, time });
    return entries;
  }, []);
}

function scrollLogToTail(overlay) {
  overlay.style.setProperty("--log-tail-padding", "0px");
  overlay.scrollTop = overlay.scrollHeight;

  const overlayTop = overlay.getBoundingClientRect().top;
  const entries = [...overlay.children];
  const firstVisibleIndex = entries.findIndex((entry) => entry.getBoundingClientRect().bottom > overlayTop + 0.5);
  const firstVisible = entries[firstVisibleIndex];
  const nextEntry = entries[firstVisibleIndex + 1];

  if (!firstVisible || !nextEntry || firstVisible.getBoundingClientRect().top >= overlayTop - 0.5) return;

  const tailPadding = Math.max(0, Math.ceil(nextEntry.getBoundingClientRect().top - overlayTop));
  overlay.style.setProperty("--log-tail-padding", `${tailPadding}px`);
  overlay.scrollTop = overlay.scrollHeight;
}

function getInitialTheme() {
  const storedTheme = window.localStorage.getItem("render-node-theme");
  if (storedTheme === "light" || storedTheme === "dark") return storedTheme;
  return "dark";
}

function useSecondClock(active) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const immediate = window.setTimeout(() => setNow(Date.now()), 0);
    if (!active) return undefined;
    const interval = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      window.clearTimeout(immediate);
      window.clearInterval(interval);
    };
  }, [active]);

  return now;
}

function useRenderTiming(job) {
  const [timingState, setTimingState] = useState(() => {
    const smoother = new EtaSmoother();
    return {
      smoother,
      timing: renderTimingPresentation(job, Date.now(), smoother),
    };
  });
  const updateTiming = useEffectEvent(() => {
    setTimingState((current) => {
      const smoother = new EtaSmoother(current.smoother.alpha);
      smoother.key = current.smoother.key;
      smoother.monotonicProgress = current.smoother.monotonicProgress;
      smoother.smoothedRemaining = current.smoother.smoothedRemaining;
      return {
        smoother,
        timing: renderTimingPresentation(job, Date.now(), smoother),
      };
    });
  });

  useEffect(() => {
    const immediate = window.setTimeout(updateTiming, 0);
    if (job.status !== "rendering") return () => window.clearTimeout(immediate);
    const interval = window.setInterval(updateTiming, 1000);
    return () => {
      window.clearTimeout(immediate);
      window.clearInterval(interval);
    };
  }, [job.id, job.finished_at, job.started_at, job.status]);

  return timingState.timing;
}

function StatusBadge({ status }) {
  const labels = {
    analyzing: "Analyzing",
    ready: "Ready",
    queued: "Queued",
    rendering: "Rendering",
    completed: "Completed",
    failed: "Failed",
    cancelled: "Cancelled",
  };
  return (
    <span className={`status-badge status-${status}`}>
      <span className="status-dot" />
      {labels[status] ?? status}
    </span>
  );
}

function Header({ activeVersion, connectionState, gpuCount, onPairDevice, onThemeChange, onVersions, queuedCount, storageWarning, theme, versionsLoading }) {
  return (
    <header className="app-header">
      <div className="brand-block">
        <div className="brand-mark" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <div>
          <div className="brand-name">Render Node</div>
          <div className="brand-caption">BLENDER WORKSPACE</div>
        </div>
      </div>

      <div className="header-controls">
        <button
          aria-describedby="pair-device-tooltip"
          aria-label="Pair another device"
          className="icon-button pair-device-trigger"
          onClick={onPairDevice}
          type="button"
        >
          <Icon name="share" />
          <span className="control-tooltip" id="pair-device-tooltip" role="tooltip">
            Pair another device — create a one-time link that grants access to this Render Node.
          </span>
        </button>
        <nav className="header-summary" aria-label="Node summary">
          <span className="summary-pill summary-queue">
            <Icon name="layers" size={15} />
            <strong>{queuedCount}</strong> queued
          </span>
          <span className="summary-pill summary-gpu">
            <Icon name="gpu" size={15} />
            <strong>{gpuCount}</strong> GPUs ready
          </span>
          <span className="summary-pill summary-time">
            <Icon name="clock" size={15} />
            <strong title="Average frame time is unavailable">—</strong> avg. frame
          </span>
          {storageWarning && (
            <span className="summary-pill summary-storage-warning" title={`${storageWarning.mountPoint} is running low on space`}>
              <Icon name="disk" size={15} />
              <strong>{storageWarning.freeGb} GB</strong> left
            </span>
          )}
        </nav>

        <div className="header-actions">
          <div className="connection-pill">
            <span className="connection-dot" />
            {connectionState === "reconnecting"
              ? "Reconnecting"
              : connectionState === "connecting"
                ? "Connecting"
                : connectionState === "blocked"
                  ? "Access lost"
                  : "Node online"}
            <span className="latency">WS</span>
          </div>
          <button className="version-button" onClick={onVersions} type="button">
            <span className="version-icon">
              <Icon name="cube" size={16} />
            </span>
            <span>
              {versionsLoading
                ? "Checking Blender versions…"
                : activeVersion
                  ? `Blender ${activeVersion}`
                  : "No active Blender version"}
            </span>
            <Icon name="down" size={15} />
          </button>
          <button className="icon-button" type="button" aria-label="Settings">
            <Icon name="settings" />
          </button>
          <div className="theme-switch" role="group" aria-label="Color theme">
            <button
              className={theme === "light" ? "active" : ""}
              onClick={() => onThemeChange("light")}
              type="button"
              aria-label="Light theme"
              aria-pressed={theme === "light"}
            >
              <Icon name="sun" size={16} />
            </button>
            <button
              className={theme === "dark" ? "active" : ""}
              onClick={() => onThemeChange("dark")}
              type="button"
              aria-label="Dark theme"
              aria-pressed={theme === "dark"}
            >
              <Icon name="moon" size={16} />
            </button>
          </div>
        </div>
      </div>
    </header>
  );
}

function DropdownField({ disabled = false, label, onChange, options, value }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  const optionRefs = useRef([]);
  const fieldId = `dropdown-${label.toLowerCase().replace(/\s+/g, "-")}`;
  const selectedIndex = Math.max(0, options.indexOf(value));

  useEffect(() => {
    if (!open) return undefined;

    const closeOnOutsidePress = (event) => {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
    };
    const closeOnEscape = (event) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      triggerRef.current?.focus();
    };

    document.addEventListener("pointerdown", closeOnOutsidePress);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePress);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  const focusOption = (index) => {
    const nextIndex = (index + options.length) % options.length;
    optionRefs.current[nextIndex]?.focus();
  };

  const openWithKeyboard = (event) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    setOpen(true);
    window.requestAnimationFrame(() => {
      focusOption(event.key === "ArrowDown" ? selectedIndex : selectedIndex - 1);
    });
  };

  const handleOptionKeyDown = (event, index) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      focusOption(index + (event.key === "ArrowDown" ? 1 : -1));
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      focusOption(event.key === "Home" ? 0 : options.length - 1);
    }
  };

  const selectOption = (option) => {
    onChange(option);
    setOpen(false);
    triggerRef.current?.focus();
  };

  return (
    <div
      className={`select-field dropdown-field ${open ? "open" : ""}`}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
      ref={rootRef}
    >
      <span id={`${fieldId}-label`}>{label}</span>
      <div className="dropdown-shell">
        <button
          aria-controls={`${fieldId}-menu`}
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-label={`${label}: ${value}`}
          className="dropdown-trigger"
          disabled={disabled}
          onClick={() => setOpen((current) => !current)}
          onKeyDown={openWithKeyboard}
          ref={triggerRef}
          type="button"
        >
          <span>{value}</span>
          <span className="dropdown-chevron"><Icon name="down" size={15} /></span>
        </button>
        <div
          aria-hidden={!open}
          aria-labelledby={`${fieldId}-label`}
          className="dropdown-menu"
          id={`${fieldId}-menu`}
          role="listbox"
        >
          {options.map((option, index) => (
            <button
              aria-selected={option === value}
              className={`dropdown-option ${option === value ? "selected" : ""}`}
              key={option}
              onClick={() => selectOption(option)}
              onKeyDown={(event) => handleOptionKeyDown(event, index)}
              ref={(element) => { optionRefs.current[index] = element; }}
              role="option"
              tabIndex={open ? 0 : -1}
              type="button"
            >
              {option}
              <span className="dropdown-option-check">{option === value && <Icon name="check" size={14} />}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function JobSetup({
  activeVersion,
  devices,
  job,
  onCancel,
  onRerender,
  onSave,
  onSceneUpload,
  onStart,
  onVersions,
  operationError,
  operationPending,
  runner,
  versionsLoading,
}) {
  const [engine, setEngine] = useState(job.engine ?? "Cycles");
  const [device, setDevice] = useState(
    job.id === "draft" && devices.length === 0 ? "CPU" : (job.device ?? "OptiX"),
  );
  const [frameMode, setFrameMode] = useState(job.frame_mode?.toLowerCase() ?? "range");
  const [selectedGpus, setSelectedGpus] = useState(
    job.id === "draft" ? null : (job.gpu_ids ?? null),
  );
  const [gpuRenderMode, setGpuRenderMode] = useState(
    job.gpu_render_mode ?? (job.id === "draft" ? FRAME_PER_GPU : ALL_GPUS_PER_FRAME),
  );
  const [fileName, setFileName] = useState(job.file);
  const [frameStart, setFrameStart] = useState(job.frame_start ?? 1);
  const [frameEnd, setFrameEnd] = useState(job.frame_end ?? 240);
  const [dirty, setDirty] = useState(false);
  const analysisRevision = `${job.analysis_id ?? "legacy"}:${job.analysis_status ?? "none"}`;
  const [loadedAnalysisRevision, setLoadedAnalysisRevision] = useState(analysisRevision);
  const fileInput = useRef(null);

  // Apply analysis results without replacing the file input: a replacement
  // picker may still be open when the current analysis finishes.
  if (loadedAnalysisRevision !== analysisRevision) {
    setLoadedAnalysisRevision(analysisRevision);
    setEngine(job.engine ?? "Cycles");
    setDevice(job.device ?? "OptiX");
    setFrameMode(job.frame_mode?.toLowerCase() ?? "range");
    setSelectedGpus(job.gpu_ids ?? null);
    setGpuRenderMode(job.gpu_render_mode ?? ALL_GPUS_PER_FRAME);
    setFileName(job.file);
    setFrameStart(job.frame_start ?? 1);
    setFrameEnd(job.frame_end ?? 240);
    setDirty(false);
  }
  const isActive = job.status === "queued" || job.status === "rendering";
  const isDraft = job.id === "draft";
  const editable = isDraft || job.status === "created" || job.status === "ready";
  const rerenderable = ["completed", "failed", "cancelled"].includes(job.status);
  const runtimeUnavailable = !activeVersion;
  const analyzing = ["pending", "running"].includes(job.analysis_status);
  const fileDisabled = !editable || operationPending || runtimeUnavailable;
  const controlsDisabled = fileDisabled || analyzing;
  const usesCycles = engine === "Cycles";
  const activeGpuIds = selectedGpus ?? devices.map((gpu) => gpu.id);
  const allocatedGpuIds = device === "CPU" || !usesCycles ? [] : activeGpuIds;
  const gpuRenderModeVisible = usesCycles && supportsFramePerGpu(device, allocatedGpuIds);
  const computeDevices = devices.length > 0 ? COMPUTE_DEVICES : [...new Set([device, "CPU"])];
  const runtimeVersion = isDraft ? activeVersion : (job.version ?? job.blender_version);
  const runnerUnavailable = !runner.available;

  const markChanged = (setter) => (value) => {
    setter(value);
    setDirty(true);
  };

  const configuration = () => ({
    name: job.name,
    blender_version: runtimeVersion,
    engine: ENGINE_API_VALUES[engine],
    device: usesCycles ? device.toUpperCase() : "CPU",
    gpu_ids: allocatedGpuIds,
    gpu_render_mode: usesCycles ? normalizeGpuRenderMode(device, activeGpuIds, gpuRenderMode) : ALL_GPUS_PER_FRAME,
    frame_mode: frameMode.toUpperCase(),
    frame_start: frameMode === "all" ? null : Number(frameStart),
    frame_end: frameMode === "range" ? Number(frameEnd) : null,
  });

  const toggleGpu = (gpuId) => {
    const nextSelection = (
      activeGpuIds.includes(gpuId)
        ? activeGpuIds.filter((id) => id !== gpuId)
        : [...activeGpuIds, gpuId]
    );
    if (
      isDraft
      && !supportsFramePerGpu(device, activeGpuIds)
      && supportsFramePerGpu(device, nextSelection)
    ) {
      setGpuRenderMode(FRAME_PER_GPU);
    }
    setSelectedGpus(nextSelection);
    setDirty(true);
  };

  const changeDevice = (nextDevice) => {
    if (
      isDraft
      && !supportsFramePerGpu(device, activeGpuIds)
      && supportsFramePerGpu(nextDevice, activeGpuIds)
    ) {
      setGpuRenderMode(FRAME_PER_GPU);
    }
    setDevice(nextDevice);
    setDirty(true);
  };

  const syncSavedGpuRenderMode = (savedJob) => {
    if (savedJob?.gpu_render_mode) setGpuRenderMode(savedJob.gpu_render_mode);
  };

  const save = async () => {
    try {
      const savedJob = await onSave(job.id, configuration());
      syncSavedGpuRenderMode(savedJob);
      setDirty(false);
    } catch {
      // The mutation exposes its user-facing error below the action controls.
    }
  };

  const start = async () => {
    try {
      const startedJob = await onStart(job.id, configuration());
      syncSavedGpuRenderMode(startedJob);
      setDirty(false);
    } catch {
      // The mutation exposes its user-facing error below the action controls.
    }
  };

  return (
    <section className={`panel setup-panel ${editable ? "is-editable" : "is-locked"}`}>
      <SectionHeading
        action={editable
          ? <span className="setup-mode editable-mode">Editable</span>
          : <span className="setup-mode"><Icon name="lock" size={12} /> Read only</span>}
        eyebrow={isDraft ? "New render" : "Selected job"}
        title="Job setup"
      />

      <div className="field-group">
        <div className="field-label-row">
          <label>Scene file</label>
          <span>BLEND / ZIP</span>
        </div>
        <input
          ref={fileInput}
          type="file"
          accept=".blend,.zip"
          className="sr-only"
          disabled={fileDisabled}
          onChange={async (event) => {
            const input = event.currentTarget;
            const nextFile = input.files?.[0];
            if (!nextFile) return;
            const nextConfiguration = configuration();
            if (isDraft) nextConfiguration.name = nextFile.name.replace(/\.(blend|zip)$/i, "");
            try {
              const uploadedJob = await onSceneUpload(job, nextFile, nextConfiguration);
              // Analysis owns the configuration once this upload starts it.
              if (!uploadedJob?.analysis_id) syncSavedGpuRenderMode(uploadedJob);
              setFileName(uploadedJob?.file ?? nextFile.name);
              setDirty(false);
            } catch {
              // The mutation exposes its user-facing error below the action controls.
            } finally {
              input.value = "";
            }
          }}
        />
        <button
          className="file-drop"
          disabled={fileDisabled}
          onClick={() => fileInput.current?.click()}
          type="button"
        >
          <span className="file-icon">
            <Icon name="file" size={20} />
          </span>
          <span className="file-copy">
            <strong>{fileName}</strong>
            <small aria-live="polite">
              {operationPending
                ? "Saving…"
                : analyzing
                  ? "Analyzing scene…"
                : runtimeUnavailable
                  ? "Activate a Blender version before uploading"
                : !editable
                  ? "Source locked after first render start"
                  : job.status === "ready"
                    ? "Ready to render"
                    : "Select a scene to upload"}
            </small>
          </span>
          <span className="replace-file">
            {runtimeUnavailable
              ? "Blocked"
              : editable
                ? (fileName === draftJob.file ? "Choose" : "Replace")
                : "Locked"}
          </span>
        </button>
        {analyzing && (
          <p className="scene-analysis-note" role="status">Reading scene settings and checking render devices…</p>
        )}
        {!analyzing && job.analysis_status === "failed" && (
          <p className="scene-analysis-note analysis-failed" role="status">
            Automatic setup unavailable. {job.analysis_error} You can edit the settings below.
          </p>
        )}
        {!analyzing && job.auto_configured && !dirty && (
          <p className="scene-analysis-note" role="status">Settings selected automatically. You can change them below.</p>
        )}
      </div>

      <div className="two-column-fields">
        <DropdownField disabled={controlsDisabled} label="Render engine" onChange={markChanged(setEngine)} options={RENDER_ENGINES} value={engine} />
        <DropdownField disabled={controlsDisabled || !usesCycles} label="Compute device" onChange={changeDevice} options={usesCycles ? computeDevices : ["Automatic"]} value={usesCycles ? device : "Automatic"} />
      </div>

      <div className="field-group">
        <div className="field-label-row">
          <label>Frames</label>
          <span>{job.scene_metadata
            ? `${job.scene_metadata.frame_start}–${job.scene_metadata.frame_end} · STEP ${job.scene_metadata.frame_step}`
            : "SCENE RANGE UNKNOWN"}</span>
        </div>
        <div
          className="segmented-control"
          role="group"
          aria-label="Frame mode"
          style={{ "--segment-index": FRAME_MODES.indexOf(frameMode) }}
        >
          {FRAME_MODES.map((mode) => (
            <button
              aria-pressed={frameMode === mode}
              className={frameMode === mode ? "active" : ""}
              disabled={controlsDisabled}
              key={mode}
              onClick={() => markChanged(setFrameMode)(mode)}
              type="button"
            >
              {mode === "single" ? "Single" : mode === "range" ? "Range" : "All"}
            </button>
          ))}
        </div>
        {frameMode === "range" && (
          <div className="frame-range">
            <label>
              <span>Start</span>
              <input disabled={controlsDisabled} inputMode="numeric" onChange={(event) => markChanged(setFrameStart)(event.target.value)} value={frameStart} />
            </label>
            <span className="range-line" />
            <label>
              <span>End</span>
              <input disabled={controlsDisabled} inputMode="numeric" onChange={(event) => markChanged(setFrameEnd)(event.target.value)} value={frameEnd} />
            </label>
          </div>
        )}
        {frameMode === "single" && (
          <div className="frame-range single-frame">
            <label>
              <span>Frame</span>
              <input disabled={controlsDisabled} inputMode="numeric" onChange={(event) => markChanged(setFrameStart)(event.target.value)} value={frameStart} />
            </label>
          </div>
        )}
      </div>

      {gpuRenderModeVisible && (
        <div className="field-group gpu-render-mode-field">
          <div className="field-label-row">
            <label>GPU render mode</label>
          </div>
          <div
            aria-label="GPU render mode"
            className="segmented-control"
            role="group"
            style={{
              "--segment-count": 2,
              "--segment-index": gpuRenderMode === FRAME_PER_GPU ? 0 : 1,
            }}
          >
            <button
              aria-pressed={gpuRenderMode === FRAME_PER_GPU}
              className={gpuRenderMode === FRAME_PER_GPU ? "active" : ""}
              disabled={controlsDisabled}
              onClick={() => markChanged(setGpuRenderMode)(FRAME_PER_GPU)}
              type="button"
            >
              Frame per GPU
            </button>
            <button
              aria-pressed={gpuRenderMode === ALL_GPUS_PER_FRAME}
              className={gpuRenderMode === ALL_GPUS_PER_FRAME ? "active" : ""}
              disabled={controlsDisabled}
              onClick={() => markChanged(setGpuRenderMode)(ALL_GPUS_PER_FRAME)}
              type="button"
            >
              All GPUs per frame
            </button>
          </div>
        </div>
      )}

      <div className="field-group gpu-field">
        <div className="field-label-row">
          <label>GPU allocation</label>
          <span>{!usesCycles ? "MANAGED BY BLENDER" : device === "CPU" ? "NOT USED · CPU MODE" : `${allocatedGpuIds.length} SELECTED`}</span>
        </div>
        <div className="gpu-list">
          {devices.length === 0 && <span className="gpu-empty">{usesCycles ? "No NVIDIA GPUs discovered · CPU mode" : "Graphics device is selected by Blender"}</span>}
          {devices.map((gpu) => {
            const selected = allocatedGpuIds.includes(gpu.id);
            return (
              <button
                aria-pressed={selected}
                className={`gpu-option ${selected ? "selected" : ""}`}
                disabled={controlsDisabled || !usesCycles || device === "CPU"}
                key={gpu.id}
                onClick={() => toggleGpu(gpu.id)}
                type="button"
              >
                <span className="gpu-check">
                  {selected && <Icon name="check" size={13} />}
                </span>
                <span className="gpu-index">0{gpu.id}</span>
                <span className="gpu-name">{gpu.name.replace("NVIDIA ", "")}</span>
                <span className="gpu-memory">{gpu.memoryTotal} GB</span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="setup-footer">
        <div className="setup-error-slot">
          {operationError && <p className="setup-error" role="alert">{operationError}</p>}
        </div>

        <div className={`active-runtime ${runtimeUnavailable ? "unavailable" : ""}`}>
          <span>
            <Icon name="cube" size={15} />
            {isDraft ? "Active runtime" : "Job runtime"}
          </span>
          <strong>
            {isDraft && versionsLoading
              ? "Checking versions…"
              : runtimeVersion
                ? `Blender ${runtimeVersion}`
                : "No active Blender version"}
          </strong>
        </div>

        {runtimeUnavailable && !versionsLoading && (
          <div className="runtime-empty" role="status">
            <span>Download, install and activate Blender to enable render actions.</span>
            <button onClick={onVersions} type="button">Manage versions</button>
          </div>
        )}

        <div className={`runner-status ${runner.available ? "available" : "unavailable"}`} role="status">
          <span className="runner-status-dot" />
          <span>{runner.message}</span>
        </div>

        {job.status === "ready" && editable && (
          <div className="setup-actions">
            <button className="secondary-action" disabled={!dirty || controlsDisabled} onClick={save} type="button">
              Save changes
            </button>
            <button aria-busy={operationPending || analyzing} className="primary-action" disabled={controlsDisabled || runnerUnavailable} onClick={start} type="button">
              <Icon name="play" size={18} /> Start render
            </button>
          </div>
        )}
        {(isDraft || job.status === "created") && (
          <button className="primary-action" disabled type="button">
            <Icon name="upload" size={18} /> Upload scene to continue
          </button>
        )}
        {isActive && (
          <button aria-busy={operationPending} className="primary-action danger-action" disabled={operationPending} onClick={() => void onCancel(job.id).catch(() => undefined)} type="button">
            <Icon name="stop" size={18} />
            {job.status === "rendering" ? "Cancel render" : "Cancel job"}
          </button>
        )}
        {rerenderable && (
          <button aria-busy={operationPending} className="primary-action" disabled={operationPending || runtimeUnavailable} onClick={() => void onRerender(job.id).catch(() => undefined)} type="button">
            <Icon name="repeat" size={18} /> Rerender as new job
          </button>
        )}
      </div>
    </section>
  );
}

function RenderFrameVisual() {
  return (
    <>
      <div className="viewport-grid" />
      <div className="scene-glow scene-glow-left" />
      <div className="scene-glow scene-glow-right" />
      <div className="scene-platform">
        <div className="scene-object object-back" />
        <div className="scene-object object-main">
          <div className="object-cutout" />
        </div>
        <div className="scene-object object-front" />
        <div className="scene-sphere" />
      </div>
    </>
  );
}

function FramePreviewModal({ frameNumber, imageUrl, onClose }) {
  const closeButton = useRef(null);
  const dialogRef = useDialogBehavior(onClose, closeButton);
  const entered = useModalEntrance();

  return (
    <div className={`modal-backdrop frame-preview-backdrop ${entered ? "is-entered" : ""}`} onMouseDown={onClose} role="presentation">
      <section
        aria-labelledby="frame-preview-title"
        aria-modal="true"
        className="frame-preview-modal"
        onMouseDown={(event) => event.stopPropagation()}
        ref={dialogRef}
        role="dialog"
      >
        <h2 className="sr-only" id="frame-preview-title">Frame {frameNumber} full resolution</h2>
        {imageUrl ? <img alt={`Frame ${frameNumber} full resolution`} src={imageUrl} /> : <RenderFrameVisual />}
        <span className="expanded-frame-number">FRAME {frameNumber}</span>
        <button
          aria-label="Close full resolution frame"
          className="frame-preview-close icon-button"
          onClick={onClose}
          ref={closeButton}
          type="button"
        >
          <Icon name="close" />
        </button>
      </section>
    </div>
  );
}

function ProgressTrack({ accessibleLabel, progress }) {
  return (
    <div
      aria-label={`${accessibleLabel} ${progress}%`}
      aria-valuemax="100"
      aria-valuemin="0"
      aria-valuenow={progress}
      className="progress-track"
      role="progressbar"
    >
      <span style={{ width: `${progress}%` }} />
    </div>
  );
}

function ProgressRow({ row, timing = null, tone = "worker" }) {
  const samples = Number.isInteger(row.sample) && Number.isInteger(row.totalSamples)
    ? `${row.sample} / ${row.totalSamples}`
    : "—";
  const title = row.gpuUuid
    ? `${row.label} · ${row.deviceName} · ${row.gpuUuid}`
    : row.label;

  return (
    <div className={`progress-row progress-row-${tone}`} data-progress-row={row.key}>
      <div className="progress-row-copy">
        <span className="progress-row-identity" title={title}>
          <strong>{row.label}</strong>
          <small>{row.state}</small>
        </span>
        <span className="progress-row-detail">
          <span><small>FRAME</small>{row.frame ?? "—"}</span>
          <span><small>SAMPLES</small>{samples}</span>
        </span>
        {timing && (
          <span className="progress-row-timing">
            <span><small>ELAPSED</small>{timing.elapsed}</span>
            <span><small>ETA</small>{timing.remaining}</span>
          </span>
        )}
        <b>{row.progress}%</b>
      </div>
      <ProgressTrack accessibleLabel={row.accessibleLabel} progress={row.progress} />
    </div>
  );
}

function RenderPreview({ devices = [], isMockMode, job, liveLogs = [], mockLogLines = [], sampleProgress = null }) {
  const analyzing = ["pending", "running"].includes(job.analysis_status);
  const [frameOpen, setFrameOpen] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const [logPanelOpen, setLogPanelOpen] = useState(false);
  const [logTabOpen, setLogTabOpen] = useState(false);
  const logOverlayRef = useRef(null);
  const logPanelOpenRef = useRef(false);
  const logSequenceTimerRef = useRef(null);
  const logTabOpenRef = useRef(false);
  const logToggleRef = useRef(null);
  const followLogTailRef = useRef(true);
  const isRendering = job.status === "rendering";
  const timing = useRenderTiming(job);
  const canLoadOutput = !isMockMode && job.id !== "draft";
  const framesQuery = useQuery({
    queryKey: ["frames", job.id, 1, FRAMES_PER_PAGE],
    queryFn: ({ signal }) => artifactsApi.frames(job.id, { page: 1, pageSize: FRAMES_PER_PAGE, signal }),
    enabled: canLoadOutput,
  });
  const logQuery = useQuery({
    queryKey: ["log-tail", job.id],
    queryFn: ({ signal }) => artifactsApi.logTail(job.id, { lines: 100, signal }),
    enabled: canLoadOutput && ["rendering", "completed", "failed", "cancelled"].includes(job.status),
    retry: false,
  });
  const availableFrames = framesQuery.data?.items ?? [];
  const latestFrame = availableFrames.find((frame) => frame.frame === job.current_frame)
    ?? availableFrames.at(-1);
  const hasOutput = isMockMode
    ? job.status === "completed" || isRendering
    : Boolean(latestFrame?.preview_url);
  const frameReady = isMockMode ? job.status === "completed" : Boolean(latestFrame?.original_url);
  const currentFrame = isRendering
    ? sampleProgress?.frame ?? job.current_frame ?? latestFrame?.frame ?? job.frame_start ?? 1
    : latestFrame?.frame ?? job.current_frame ?? job.frame_start ?? 1;
  const shownProgress = isRendering
    ? Math.max(job.progress, sampleProgress?.progress ?? 0)
    : job.status === "completed" ? 100 : 0;
  const sampleMatchesFrame = sampleProgress?.frame === null
    || sampleProgress?.frame === undefined
    || sampleProgress.frame === currentFrame;
  const shownSamples = isRendering
    && sampleMatchesFrame
    && Number.isInteger(sampleProgress?.sample)
    && Number.isInteger(sampleProgress?.totalSamples)
    ? `${sampleProgress.sample} / ${sampleProgress.totalSamples}`
    : "—";
  const progressRows = buildProgressRows(job, devices, sampleProgress);
  const frameNumber = paddedFrame(currentFrame, 3);
  const rawDisplayedLogs = isMockMode
    ? mockLogLines
    : (liveLogs.length ? liveLogs : (logQuery.data?.lines ?? []))
      .map((line) => ["", line]);
  const displayedLogs = compactLogEntries(rawDisplayedLogs);
  const latestDisplayedLog = `${rawDisplayedLogs.length}\u0000${rawDisplayedLogs.at(-1)?.join("\u0000") ?? ""}`;

  useEffect(() => {
    followLogTailRef.current = true;
  }, [job.id]);

  useEffect(() => {
    window.clearTimeout(logSequenceTimerRef.current);

    if (logOpen) {
      const tabWasOpen = logTabOpenRef.current;
      if (!tabWasOpen) {
        logTabOpenRef.current = true;
        setLogTabOpen(true);
      }

      if (!logPanelOpenRef.current) {
        const openPanel = () => {
          logPanelOpenRef.current = true;
          setLogPanelOpen(true);
        };
        if (tabWasOpen) openPanel();
        else logSequenceTimerRef.current = window.setTimeout(openPanel, LOG_TAB_TRANSITION_FALLBACK_MS);
      }
    } else if (logPanelOpenRef.current) {
      logPanelOpenRef.current = false;
      setLogPanelOpen(false);
      logSequenceTimerRef.current = window.setTimeout(() => {
        logTabOpenRef.current = false;
        setLogTabOpen(false);
      }, LOG_PANEL_TRANSITION_FALLBACK_MS);
    } else if (logTabOpenRef.current) {
      logTabOpenRef.current = false;
      setLogTabOpen(false);
    }

    return () => window.clearTimeout(logSequenceTimerRef.current);
  }, [logOpen]);

  useEffect(() => {
    const overlay = logOverlayRef.current;
    if (!overlay || !followLogTailRef.current) return undefined;
    const alignmentFrame = window.requestAnimationFrame(() => scrollLogToTail(overlay));
    return () => window.cancelAnimationFrame(alignmentFrame);
  }, [hasOutput, job.id, latestDisplayedLog]);

  const handleLogScroll = (event) => {
    const overlay = event.currentTarget;
    followLogTailRef.current = overlay.scrollHeight - overlay.scrollTop - overlay.clientHeight <= 24;
  };
  const handleLogPanelTransitionEnd = (event) => {
    if (event.target !== event.currentTarget || event.propertyName !== "transform") return;
    if (logOpen || logPanelOpenRef.current || !logTabOpenRef.current) return;
    window.clearTimeout(logSequenceTimerRef.current);
    logTabOpenRef.current = false;
    setLogTabOpen(false);
  };
  const handleLogTabTransitionEnd = (event) => {
    if (event.propertyName !== "clip-path" || event.nativeEvent.pseudoElement !== "::before") return;
    if (!logOpen || !logTabOpenRef.current || logPanelOpenRef.current) return;
    window.clearTimeout(logSequenceTimerRef.current);
    logPanelOpenRef.current = true;
    setLogPanelOpen(true);
  };
  const handleLogDrawerKeyDown = (event) => {
    if (event.key !== "Escape" || !logOpen) return;
    event.preventDefault();
    setLogOpen(false);
    window.requestAnimationFrame(() => logToggleRef.current?.focus());
  };
  const currentTask = analyzing ? "Analyzing scene…" : isRendering
    ? `Rendering frame ${currentFrame}`
    : job.status === "queued"
      ? "Waiting for the render worker"
      : job.status === "completed"
        ? "Render completed"
        : job.status === "failed"
          ? "Render failed"
          : job.status === "cancelled"
            ? "Render cancelled"
            : "Ready to start";

  return (
    <>
      <section className="panel preview-panel">
        <div className="preview-header">
          <div className="preview-title">
            <Icon name="image" size={16} /> Preview
          </div>
          <div className="preview-meta">
            <StatusBadge status={analyzing ? "analyzing" : job.status} />
          </div>
        </div>

        <div className={`render-viewport ${hasOutput ? "has-output" : ""}`}>
          {latestFrame?.preview_url
            ? <img alt={`Preview frame ${currentFrame}`} className="render-frame-image" src={latestFrame.preview_url} />
            : <RenderFrameVisual />}
          {!hasOutput && (
            <div className="preview-empty">
              <span className="empty-icon"><Icon name="image" size={24} /></span>
              <strong>Preview will appear here</strong>
              <small>{analyzing ? "Preparing scene settings before rendering." : "Start a ready job to run Blender on this node."}</small>
            </div>
          )}
          {hasOutput && (
            <>
              <div className="viewport-topline">
                <span>CAMERA 01</span>
                <span>1920 × 1080 · 100%</span>
              </div>
              <div
                className={`preview-log-drawer ${logPanelOpen ? "is-panel-open" : ""}`}
                onKeyDown={handleLogDrawerKeyDown}
              >
                <div className="preview-log-shell" inert={!logPanelOpen} onTransitionEnd={handleLogPanelTransitionEnd}>
                  <div
                    aria-hidden={!logPanelOpen}
                    aria-label="Blender live log"
                    className="preview-log-overlay"
                    id="preview-live-log"
                    onScroll={handleLogScroll}
                    ref={logOverlayRef}
                    role="log"
                    tabIndex={logPanelOpen ? 0 : -1}
                  >
                    {displayedLogs.map(({ count, level, line, time }, index, lines) => (
                      <div
                        className={`log-entry log-level-${level} ${time ? "has-time" : ""} ${count > 1 ? "is-repeated" : ""} ${index === lines.length - 1 ? "latest" : ""}`}
                        key={`${index}-${time}-${line}`}
                      >
                        {time && <time>{time}</time>}
                        <code>{line}</code>
                        {count > 1 && <span aria-label={`Repeated ${count} times`} className="log-repeat-count">×{count}</span>}
                      </div>
                    ))}
                  </div>
                </div>
                <button
                  aria-controls="preview-live-log"
                  aria-expanded={logOpen}
                  aria-label={logOpen ? "Hide Blender live log" : "Show Blender live log"}
                  className={`preview-log-toggle ${logTabOpen ? "is-open" : ""}`}
                  onClick={() => setLogOpen((open) => !open)}
                  onKeyDown={handleLogDrawerKeyDown}
                  onTransitionEnd={handleLogTabTransitionEnd}
                  ref={logToggleRef}
                  type="button"
                >
                  <span aria-hidden="true" className="preview-log-toggle-label">Log</span>
                  <span aria-hidden="true" className="preview-log-toggle-arrow"><Icon name="chevron" size={18} /></span>
                </button>
              </div>
              <div className="frame-chip">FRAME {frameNumber}</div>
              <div className="preview-frame-actions">
                <button aria-label={`Open frame ${frameNumber} in full resolution`} onClick={() => setFrameOpen(true)} type="button">
                  <Icon name="expand" size={17} />
                </button>
                {frameReady && (
                  <a aria-label={`Download frame ${frameNumber}`} download href={latestFrame?.original_url ?? "#"} role="button">
                    <Icon name="download" size={17} />
                  </a>
                )}
              </div>
            </>
          )}
        </div>

        <div className={`render-progress-block render-progress-${progressRows.kind}`}>
          {progressRows.kind === "single" ? (
            <>
              <div className="render-progress-copy">
                <div className="current-task-copy">
                  <span className="eyebrow">Current task</span>
                  <strong>{currentTask}</strong>
                  <span className="current-task-timing">
                    <span><small>ELAPSED</small>{timing.elapsed}</span>
                    <span><small>ETA</small>{timing.remaining}</span>
                  </span>
                </div>
                <div className="progress-stats">
                  <span className="progress-stat-frame"><small>FRAME</small>{isRendering ? currentFrame : "—"}</span>
                  <span className="progress-stat-samples"><small>SAMPLES</small>{shownSamples}</span>
                  <span className="progress-stat-state"><small>STATE</small>{analyzing ? "analyzing" : job.status}</span>
                  <b>{shownProgress}%</b>
                </div>
              </div>
              <ProgressTrack accessibleLabel="Render progress" progress={shownProgress} />
            </>
          ) : (
            <>
              <div className="render-progress-heading">
                <div>
                  <span className="eyebrow">Current task</span>
                  <strong>{currentTask}</strong>
                </div>
                <span className="render-progress-strategy">
                  {progressRows.kind === "distributed" ? "Frame per GPU" : "Shared process"}
                </span>
              </div>
              <div className={`worker-progress-list worker-progress-list-${progressRows.kind}`}>
                {progressRows.workers.map((row) => (
                  <ProgressRow
                    key={row.key}
                    row={row}
                    timing={progressRows.kind === "all-gpus" ? timing : null}
                    tone={progressRows.kind === "all-gpus" ? "joint" : "worker"}
                  />
                ))}
              </div>
              {progressRows.overall && (
                <ProgressRow row={progressRows.overall} timing={timing} tone="overall" />
              )}
            </>
          )}
          {job.error && <p className="render-error" role="alert">{job.error}</p>}
        </div>
      </section>
      {frameOpen && (
        <FramePreviewModal
          frameNumber={frameNumber}
          imageUrl={latestFrame?.original_url}
          onClose={() => setFrameOpen(false)}
        />
      )}
    </>
  );
}

function JobQueue({
  actionError,
  deletingJobId,
  jobs,
  loading,
  onDelete,
  onNew,
  onPageChange,
  onSelect,
  page,
  pages,
  selectedJobId,
  total,
  now,
}) {
  const [revealedJobId, setRevealedJobId] = useState(null);
  const pointerStart = useRef(null);
  const suppressSelection = useRef(false);
  const suppressSelectionTimer = useRef(null);
  const firstShown = total === 0 ? 0 : ((page - 1) * JOBS_PER_PAGE) + 1;
  const lastShown = Math.min(page * JOBS_PER_PAGE, total);

  const beginSwipe = (jobId, x, y) => {
    pointerStart.current = { jobId, x, y };
    suppressSelection.current = false;
  };

  const finishSwipe = (jobId, x, y) => {
    const start = pointerStart.current;
    pointerStart.current = null;
    if (!start || start.jobId !== jobId) return false;
    const deltaX = x - start.x;
    const deltaY = y - start.y;
    if (Math.abs(deltaX) < 42 || Math.abs(deltaX) <= Math.abs(deltaY)) return false;
    suppressSelection.current = true;
    window.clearTimeout(suppressSelectionTimer.current);
    suppressSelectionTimer.current = window.setTimeout(() => {
      suppressSelection.current = false;
    }, 120);
    setRevealedJobId(deltaX < 0 ? jobId : null);
    return true;
  };

  const selectJob = (jobId) => {
    if (suppressSelection.current) {
      suppressSelection.current = false;
      return;
    }
    setRevealedJobId(null);
    onSelect(jobId);
  };

  const requestDelete = (job) => {
    setRevealedJobId(null);
    onDelete(job);
  };

  return (
    <section aria-busy={loading} className="panel queue-panel">
      <SectionHeading
        eyebrow="Workspace"
        title="Jobs"
        action={(
          <div className="queue-heading-actions">
            <span className="queue-count">{total}</span>
            <button className={`new-job-button ${selectedJobId === "new" ? "active" : ""}`} onClick={onNew} type="button">
              <Icon name="plus" size={15} /> New job
            </button>
          </div>
        )}
      />
      <div className="job-list">
        {jobs.map((job) => {
          const active = job.status === "queued" || job.status === "rendering";
          const revealed = revealedJobId === job.id;
          const duration = formatDuration(renderDurationSeconds(job, now));
          return (
            <div className={`job-row-shell ${revealed ? "delete-revealed" : ""}`} key={job.id}>
              <button
                aria-label={active ? `Cannot delete active job ${job.name}` : `Delete job ${job.name}`}
                className="job-delete-action"
                disabled={active || deletingJobId === job.id}
                onClick={() => requestDelete(job)}
                onTouchEnd={(event) => {
                  event.preventDefault();
                  requestDelete(job);
                }}
                title={active ? "Cancel this job before deleting it" : `Delete ${job.name}`}
                type="button"
              >
                <Icon name="trash" size={18} />
              </button>
              <div
                className={`job-row ${selectedJobId === job.id ? "selected" : ""}`}
                onPointerDown={(event) => {
                  if (event.pointerType !== "touch") beginSwipe(job.id, event.clientX, event.clientY);
                }}
                onPointerUp={(event) => {
                  if (event.pointerType !== "touch") finishSwipe(job.id, event.clientX, event.clientY);
                }}
                onTouchEnd={(event) => {
                  const touch = event.changedTouches[0];
                  if (!touch) return;
                  const swiped = finishSwipe(job.id, touch.clientX, touch.clientY);
                  if (!swiped && event.target.closest?.(".job-select-button")) {
                    event.preventDefault();
                    selectJob(job.id);
                  }
                }}
                onTouchStart={(event) => {
                  const touch = event.touches[0];
                  if (touch) beginSwipe(job.id, touch.clientX, touch.clientY);
                }}
              >
                <button className="job-select-button" onClick={() => selectJob(job.id)} type="button">
                  <span className="job-status-rail" data-status={job.status} />
                  <span className="job-main">
                    <span className="job-title-line">
                      <strong>{job.name}</strong>
                      <small>#{job.shortId}</small>
                    </span>
                    <span className="job-detail">{job.engine} · {job.engine === "Cycles" ? job.device : "Automatic device"} · {job.frame}</span>
                    <span className="job-bottom-line">
                      <StatusBadge status={["pending", "running"].includes(job.analysis_status) ? "analyzing" : job.status} />
                      <small className="job-timing">
                        <span>{job.created}</span>
                        <span aria-hidden="true"> · </span>
                        <span
                          aria-label={`Render duration ${duration}`}
                          className="job-duration"
                          title={`Render duration: ${duration}`}
                        >
                          {duration}
                        </span>
                      </small>
                    </span>
                    {job.status === "rendering" && (
                      <span className="job-mini-progress"><i style={{ width: `${job.progress}%` }} /></span>
                    )}
                  </span>
                </button>
                <button
                  aria-expanded={revealed}
                  aria-label={`${revealed ? "Hide" : "Reveal"} delete action for ${job.name}`}
                  className="job-reveal-button"
                  onClick={() => setRevealedJobId((current) => current === job.id ? null : job.id)}
                  type="button"
                >
                  <Icon name="chevron" size={15} />
                </button>
              </div>
            </div>
          );
        })}
        {!loading && jobs.length === 0 && (
          <div className="queue-empty">No jobs on this page</div>
        )}
      </div>
      {actionError && <p className="queue-action-error" role="alert">{actionError}</p>}
      {pages > 1 && (
        <nav aria-label="Jobs pages" className="queue-pagination">
          <button aria-label="Previous jobs page" disabled={page <= 1 || loading} onClick={() => {
            setRevealedJobId(null);
            onPageChange(page - 1);
          }} type="button">
            <Icon name="chevron" size={14} />
          </button>
          <span>
            <strong>{page} / {pages}</strong>
            <small>{firstShown}–{lastShown} of {total}</small>
          </span>
          <button aria-label="Next jobs page" disabled={page >= pages || loading} onClick={() => {
            setRevealedJobId(null);
            onPageChange(page + 1);
          }} type="button">
            <Icon name="chevron" size={14} />
          </button>
        </nav>
      )}
    </section>
  );
}

function DeleteJobDialog({ error, job, onClose, onConfirm, pending }) {
  const entered = useModalEntrance();
  const cancelButton = useRef(null);
  const dialogRef = useDialogBehavior(onClose, cancelButton);

  return (
    <div className={`modal-backdrop ${entered ? "is-entered" : ""}`} role="presentation" onMouseDown={pending ? undefined : onClose}>
      <section
        aria-describedby="delete-job-description"
        aria-labelledby="delete-job-title"
        aria-modal="true"
        className="delete-job-modal"
        onMouseDown={(event) => event.stopPropagation()}
        ref={dialogRef}
        role="dialog"
      >
        <span className="delete-job-icon"><Icon name="trash" size={22} /></span>
        <div>
          <span className="eyebrow">Permanent deletion</span>
          <h2 id="delete-job-title">Delete “{job.name}”?</h2>
          <p id="delete-job-description">This job has artifacts. Its scene, rendered files, previews and logs will be deleted permanently.</p>
        </div>
        {error && <p className="delete-job-error" role="alert">{error}</p>}
        <div className="delete-job-actions">
          <button disabled={pending} onClick={onClose} ref={cancelButton} type="button">Cancel</button>
          <button
            className="confirm-delete-job"
            disabled={pending}
            onClick={() => void onConfirm().catch(() => undefined)}
            type="button"
          >
            <Icon name="trash" size={16} /> {pending ? "Deleting…" : "Delete job"}
          </button>
        </div>
      </section>
    </div>
  );
}

function MetricChart({ chartId, history, maximum = 100, tone }) {
  const width = 240;
  const height = 88;
  const segments = metricChartSegments(history, width, height, maximum);
  const color = `var(--${tone})`;

  return (
    <svg aria-hidden="true" className="metric-chart" data-samples={history.length} preserveAspectRatio="none" viewBox={`0 0 ${width} ${height}`}>
      <defs>
        <linearGradient id={chartId} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.38" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {segments.map((points, index) => {
        if (points.length === 1) return <circle key={index} cx={points[0].x} cy={points[0].y} r="2" fill={color} />;
        const linePath = points.map((point, pointIndex) => `${pointIndex === 0 ? "M" : "L"} ${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(" ");
        const areaPath = `${linePath} L ${points.at(-1).x} ${height} L ${points[0].x} ${height} Z`;
        return (
          <g key={index}>
            <path className="metric-area" d={areaPath} fill={`url(#${chartId})`} />
            <path className="metric-line" d={linePath} stroke={color} />
          </g>
        );
      })}
    </svg>
  );
}

function MetricCard({ chartId, history, label, maximum, percent, tone, value }) {
  const available = Number.isFinite(percent);
  return (
    <article className="metric-item">
      <div
        aria-label={`${label}: ${available ? value : "Unavailable"}`}
        className="metric-ring"
        style={{ "--metric": available ? percent : 0, "--tone": `var(--${tone})` }}
        title={available ? undefined : "Measurement unavailable"}
      >
        <div className="metric-ring-content">
          <span>{label}</span>
          <strong>{available ? value : "—"}</strong>
        </div>
      </div>
      <MetricChart chartId={chartId} history={history} maximum={maximum} tone={tone} />
    </article>
  );
}

function ResourceRow({ detail, kind, metrics, name, id }) {
  const chartKey = useId();
  return (
    <div className="resource-row" data-resource-id={id}>
      <div className="resource-identity">
        <span>{kind}</span>
        <strong>{name}</strong>
        <small>{detail}</small>
      </div>
      <div className="resource-metrics">
        {metrics.map((metric, index) => (
          <MetricCard chartId={`${chartKey}-${index}`} key={metric.label} {...metric} />
        ))}
      </div>
    </div>
  );
}

function Metrics({ devices, processors, storages, history }) {
  const gpuRows = devices.map((gpu) => ({
    id: resourceKey("gpu", gpu),
    detail: `${gpu.memoryTotal} GB VRAM`,
    kind: `GPU ${String(gpu.id + 1).padStart(2, "0")}`,
    name: gpu.name.replace("NVIDIA ", ""),
    metrics: [
      { label: "Load", value: `${gpu.utilization}%`, percent: gpu.utilization, tone: "blue", history: metricSeries(history, "gpu", gpu, "load") },
      { label: "VRAM", value: `${gpu.memoryUsed} GB`, percent: metricPercent(gpu.memoryUsed, gpu.memoryTotal), tone: "violet", history: metricSeries(history, "gpu", gpu, "memory") },
      { label: "Temp", value: `${gpu.temperature}°`, percent: gpu.temperature, tone: gpu.temperature < 80 ? "green" : "red", history: metricSeries(history, "gpu", gpu, "temperature") },
    ],
  }));
  const cpuRows = processors.map((cpu) => ({
    id: resourceKey("cpu", cpu),
    detail: `${cpu.cores} cores`,
    kind: `CPU ${String(cpu.id + 1).padStart(2, "0")}`,
    name: cpu.name,
    metrics: [
      { label: "Load", value: `${cpu.utilization}%`, percent: cpu.utilization, tone: "green", history: metricSeries(history, "cpu", cpu, "load") },
      { label: "Memory", value: `${cpu.memoryUsed} GB`, percent: metricPercent(cpu.memoryUsed, cpu.memoryTotal), tone: "orange", history: metricSeries(history, "cpu", cpu, "memory") },
      { label: "Temp", value: `${cpu.temperature}°`, percent: cpu.temperature, tone: cpu.temperature < 80 ? "green" : "red", history: metricSeries(history, "cpu", cpu, "temperature") },
    ],
  }));
  const storageRows = storages.map((storage) => {
    const usedPercent = metricPercent(storage.totalGb - storage.freeGb, storage.totalGb);
    const freePercent = (storage.freeGb / storage.totalGb) * 100;
    const capacity = storage.totalGb >= 1000 ? `${storage.totalGb / 1000} TB` : `${storage.totalGb} GB`;
    const readHistory = metricSeries(history, "storage", storage, "read");
    const writeHistory = metricSeries(history, "storage", storage, "write");
    const throughputMaximum = Math.max(
      storage.maxThroughputMbps || 1000,
      ...readHistory.map((sample) => sample.value ?? 0),
      ...writeHistory.map((sample) => sample.value ?? 0),
    );

    return {
      id: resourceKey("storage", storage),
      detail: `${storage.mountPoint} · ${storage.freeGb} GB free of ${capacity}`,
      kind: `STORAGE ${String(storage.id + 1).padStart(2, "0")}`,
      name: storage.name,
      metrics: [
        { label: "Used", value: `${Math.round(usedPercent)}%`, percent: usedPercent, tone: freePercent < 10 ? "red" : freePercent < 20 ? "orange" : "blue", history: metricSeries(history, "storage", storage, "used") },
        { label: "Read", value: `${storage.readMbps} MB/s`, percent: metricPercent(storage.readMbps, throughputMaximum), tone: "green", history: readHistory, maximum: throughputMaximum },
        { label: "Write", value: `${storage.writeMbps} MB/s`, percent: metricPercent(storage.writeMbps, throughputMaximum), tone: "violet", history: writeHistory, maximum: throughputMaximum },
      ],
    };
  });
  const resourceRows = [...gpuRows, ...cpuRows, ...storageRows];

  return (
    <section aria-label="Resource metrics" className="metrics-strip">
      <div className="metrics-scroll" style={{ "--visible-resource-rows": Math.min(2, resourceRows.length || 1) }}>
        {resourceRows.map((resource) => (
          <ResourceRow key={resource.id} {...resource} />
        ))}
        {resourceRows.length === 0 && (
          <div className="metrics-empty">No hardware metrics available</div>
        )}
      </div>
    </section>
  );
}

async function copyPairingLink(link, input) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(link);
    return;
  }
  input?.focus();
  input?.select();
  if (!document.execCommand("copy")) throw new Error("Copy is unavailable in this browser");
}

function PairDeviceDialog({ error, invitation, onClose, onRetry, pending }) {
  const closeButton = useRef(null);
  const linkInput = useRef(null);
  const dialogRef = useDialogBehavior(onClose, closeButton);
  const entered = useModalEntrance();
  const [copyState, setCopyState] = useState("");
  const link = invitation ? createPairingUrl(invitation.token) : "";
  const deviceLimitReached = error?.code === "device_limit_reached";

  const copyLink = async () => {
    setCopyState("");
    try {
      await copyPairingLink(link, linkInput.current);
      setCopyState("Link copied");
    } catch (copyError) {
      setCopyState(copyError instanceof Error ? copyError.message : "Could not copy link");
    }
  };

  return (
    <div className={`modal-backdrop ${entered ? "is-entered" : ""}`} role="presentation" onMouseDown={onClose}>
      <section
        aria-labelledby="pair-device-title"
        aria-modal="true"
        className="pair-device-modal"
        onMouseDown={(event) => event.stopPropagation()}
        ref={dialogRef}
        role="dialog"
      >
        <div className="modal-header">
          <div>
            <h2 id="pair-device-title">Pair another device</h2>
            <p>This one-time link grants full access to this Render Node and expires in 5 minutes.</p>
          </div>
          <button ref={closeButton} className="icon-button" onClick={onClose} type="button" aria-label="Close pairing dialog">
            <Icon name="close" />
          </button>
        </div>
        <div className="pair-device-content">
          <div className="pair-device-warning">
            <Icon name="lock" size={17} />
            Share the link only with the device you want to authorize. It can be used once.
          </div>
          {pending && (
            <div className="pair-device-loading" role="status">
              <span className="access-spinner" aria-hidden="true" />
              Creating a protected pairing link…
            </div>
          )}
          {error && (
            <div className="pair-device-error" role="alert">
              <strong>{deviceLimitReached ? "Device limit reached" : "Could not create pairing link"}</strong>
              <span>{deviceLimitReached ? "This Render Node already has five browser sessions." : error.message}</span>
              {!deviceLimitReached && (
                <button
                  onClick={() => {
                    setCopyState("");
                    onRetry();
                  }}
                  type="button"
                >
                  Try again
                </button>
              )}
            </div>
          )}
          {invitation && (
            <>
              <label className="pair-device-link">
                <span>One-time pairing link</span>
                <input aria-label="One-time pairing link" readOnly ref={linkInput} value={link} />
              </label>
              <div className="pair-device-actions">
                <span>
                  Expires {new Date(invitation.expires_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </span>
                <button className="primary-action" onClick={() => void copyLink()} type="button">
                  <Icon name="share" size={16} />
                  Copy link
                </button>
              </div>
              <div className="pair-copy-status" aria-live="polite">{copyState}</div>
            </>
          )}
        </div>
      </section>
    </div>
  );
}

export default function App({ mockApi = null }) {
  const isMockMode = mockApi !== null;
  const queryClient = useQueryClient();
  const versionPanelOpen = useUiStore((state) => state.versionPanelOpen);
  const setVersionPanelOpen = useUiStore((state) => state.setVersionPanelOpen);
  const selectedJobId = useUiStore((state) => state.selectedJobId);
  const setSelectedJobId = useUiStore((state) => state.setSelectedJobId);
  const [mockJobs, setMockJobs] = useState(() => mockApi?.initialJobs ?? []);
  const [jobPage, setJobPage] = useState(1);
  const [deleteConfirmationJob, setDeleteConfirmationJob] = useState(null);
  const [deleteInspectionId, setDeleteInspectionId] = useState(null);
  const [deleteInspectionError, setDeleteInspectionError] = useState("");
  const [theme, setTheme] = useState(getInitialTheme);
  const [pairPanelOpen, setPairPanelOpen] = useState(false);
  const realtime = useRenderEvents(queryClient, !isMockMode);

  const jobsQuery = useQuery({
    queryKey: ["jobs", jobPage],
    queryFn: ({ signal }) => jobsApi.page({ page: jobPage, pageSize: JOBS_PER_PAGE, signal }),
    enabled: !isMockMode,
    placeholderData: (previousData) => previousData,
    refetchInterval: (query) => query.state.data?.items?.some((job) => ["pending", "running"].includes(job.analysis_status))
      ? 1000 : realtime.connectionState === "connected" ? false : 5000,
  });
  const versionApi = mockApi ?? blenderApi;
  const versionsQuery = useQuery({ queryKey: ["versions"], queryFn: versionApi.getVersions });
  const devicesQuery = useQuery({
    queryKey: ["devices"],
    queryFn: isMockMode ? mockApi.getDevices : devicesApi.list,
  });
  const metricsQuery = useQuery({
    queryKey: ["metrics"],
    queryFn: isMockMode ? mockApi.getMetrics : systemApi.metrics,
    structuralSharing: appendMetricSample,
    refetchInterval: realtime.connectionState === "connected" ? false : 5000,
  });
  const jobsSummaryQuery = useQuery({
    queryKey: ["jobs", "summary"],
    queryFn: jobsApi.summary,
    enabled: !isMockMode,
    refetchInterval: realtime.connectionState === "connected" ? false : 5000,
  });
  const capabilitiesQuery = useQuery({
    queryKey: ["capabilities"],
    queryFn: isMockMode ? mockApi.getCapabilities : systemApi.capabilities,
  });
  const downloadMutation = useMutation({
    mutationFn: versionApi.downloadVersion,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["versions"] });
      queryClient.invalidateQueries({ queryKey: ["official-versions"] });
    },
  });
  const uploadMutation = useMutation({
    mutationFn: versionApi.uploadVersion,
    onSuccess: (uploadedVersion) => {
      if (isMockMode) {
        queryClient.setQueryData(["official-versions"], (current = []) => [
          uploadedVersion,
          ...current.filter((version) => version.version !== uploadedVersion.version),
        ]);
      } else {
        queryClient.invalidateQueries({ queryKey: ["official-versions"] });
        queryClient.invalidateQueries({ queryKey: ["versions"] });
      }
    },
  });
  const installMutation = useMutation({
    mutationFn: versionApi.installVersion,
    onSuccess: (installedVersion) => {
      queryClient.setQueryData(["official-versions"], (current = []) =>
        current.filter((version) => version.version !== installedVersion.version));
      queryClient.invalidateQueries({ queryKey: ["versions"] });
      queryClient.invalidateQueries({ queryKey: ["official-versions"] });
    },
  });
  const activateMutation = useMutation({
    mutationFn: versionApi.activateVersion,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["versions"] }),
  });
  const deleteVersionMutation = useMutation({
    mutationFn: versionApi.deleteVersion,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["versions"] });
      queryClient.invalidateQueries({ queryKey: ["official-versions"] });
    },
  });
  const pairingInvitationMutation = useMutation({
    mutationFn: mockApi?.createInvitation ?? accessApi.createInvitation,
  });

  const queriedActiveVersion = versionsQuery.data?.find((version) => version.active)?.version;
  const activeVersion = queriedActiveVersion ?? (isMockMode ? mockApi.activeVersion : null);
  const mockPageCount = Math.max(1, Math.ceil(mockJobs.length / JOBS_PER_PAGE));
  const jobs = isMockMode
    ? mockJobs.slice((jobPage - 1) * JOBS_PER_PAGE, jobPage * JOBS_PER_PAGE)
    : (jobsQuery.data?.items ?? []);
  const jobTotal = isMockMode ? mockJobs.length : (jobsQuery.data?.total ?? 0);
  const jobPages = isMockMode ? mockPageCount : Math.max(1, jobsQuery.data?.pages ?? 1);
  const updateJobCache = (updatedJob) => {
    queryClient.invalidateQueries({ queryKey: ["jobs", "summary"] });
    queryClient.setQueriesData({ queryKey: ["jobs"] }, (current) => {
      if (!current?.items?.some((job) => job.id === updatedJob.id)) return current;
      return {
        ...current,
        items: current.items.map((job) => job.id === updatedJob.id ? updatedJob : job),
      };
    });
  };
  const prependJobToFirstPage = (updatedJob) => {
    setJobPage(1);
    queryClient.setQueryData(["jobs", 1], (current) => {
      if (!current) return current;
      const alreadyPresent = current.items.some((job) => job.id === updatedJob.id);
      const items = [updatedJob, ...current.items.filter((job) => job.id !== updatedJob.id)]
        .slice(0, JOBS_PER_PAGE);
      const total = current.total + (alreadyPresent ? 0 : 1);
      return {
        ...current,
        items,
        total,
        pages: Math.ceil(total / JOBS_PER_PAGE),
      };
    });
    queryClient.invalidateQueries({ queryKey: ["jobs"] });
  };
  const sceneUploadMutation = useMutation({
    mutationFn: async ({ file, configuration, job }) => {
      if (!activeVersion) throw new Error("Activate a Blender version before uploading a scene");
      if (job.id !== "draft") {
        return jobsApi.upload(job.id, file);
      }
      const createdJob = await jobsApi.create({
        ...configuration,
        engine: "CYCLES",
        device: "CPU",
        gpu_ids: [],
        gpu_render_mode: ALL_GPUS_PER_FRAME,
        frame_mode: "ALL",
        frame_start: null,
        frame_end: null,
      });
      try {
        return await jobsApi.upload(createdJob.id, file);
      } catch (error) {
        await jobsApi.delete(createdJob.id).catch(() => undefined);
        throw error;
      }
    },
    onSuccess: (uploadedJob) => {
      prependJobToFirstPage(uploadedJob);
      setSelectedJobId(uploadedJob.id);
    },
  });
  const saveJobMutation = useMutation({
    mutationFn: ({ configuration, jobId }) => jobsApi.update(jobId, configuration),
    onSuccess: updateJobCache,
  });
  const startJobMutation = useMutation({
    mutationFn: async ({ configuration, jobId }) => {
      if (!activeVersion) throw new Error("Activate a Blender version before starting a render");
      await jobsApi.update(jobId, configuration);
      return jobsApi.start(jobId);
    },
    onSuccess: updateJobCache,
  });
  const cancelJobMutation = useMutation({
    mutationFn: jobsApi.cancel,
    onSuccess: updateJobCache,
  });
  const rerenderJobMutation = useMutation({
    mutationFn: (jobId) => {
      if (!activeVersion) throw new Error("Activate a Blender version before creating a rerender");
      return jobsApi.rerender(jobId);
    },
    onSuccess: (rerenderedJob) => {
      prependJobToFirstPage(rerenderedJob);
      setSelectedJobId(rerenderedJob.id);
    },
  });
  const deleteJobMutation = useMutation({ mutationFn: jobsApi.delete });
  const creatingNewJob = selectedJobId === "new";
  const selectedJob = creatingNewJob
    ? null
    : (jobs.find((job) => job.id === selectedJobId) ?? jobs[0] ?? null);
  const setupJob = selectedJob ?? { ...draftJob, version: activeVersion };
  const queueSummary = isMockMode ? {
    queued_count: mockJobs.filter((job) => job.status === "queued").length,
    rendering_count: mockJobs.filter((job) => job.status === "rendering").length,
  } : (jobsSummaryQuery.isError ? null : jobsSummaryQuery.data);
  const isRendering = (queueSummary?.rendering_count ?? 0) > 0;
  const clockNow = useSecondClock(isRendering);
  const hasActiveJob = !queueSummary || queueSummary.queued_count > 0 || queueSummary.rendering_count > 0;
  const metricDevices = metricsQuery.data?.devices ?? [];
  const processors = metricsQuery.data?.processors ?? [];
  const storages = metricsQuery.data?.storages ?? [];
  const storageWarning = storages.find(
    (storage) => storage.status === "low_space"
      || storage.freeGb < 50
      || storage.freeGb / storage.totalGb < 0.1,
  );
  const runner = capabilitiesQuery.data?.runner ?? {
    available: false,
    mode: "local",
    message: capabilitiesQuery.isError
      ? "Render runner status is unavailable"
      : "Checking render runner…",
  };

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("render-node-theme", theme);
  }, [theme]);

  useEffect(() => {
    if (!isMockMode || !isRendering) return undefined;
    const interval = window.setInterval(() => {
      setMockJobs((currentJobs) =>
        currentJobs.map((job) => {
          if (job.status !== "rendering") return job;
          const nextProgress = Math.min(100, job.progress + 3);
          return {
            ...job,
            progress: nextProgress,
            status: nextProgress >= 100 ? "completed" : "rendering",
            created: nextProgress >= 100 ? "Completed now" : job.created,
            finished_at: nextProgress >= 100 ? new Date().toISOString() : null,
          };
        }),
      );
    }, 650);
    return () => window.clearInterval(interval);
  }, [isMockMode, isRendering]);

  const displayJob = setupJob;
  const sampleProgress = isMockMode && displayJob.status === "rendering"
    ? (() => {
        const selectedDevices = (displayJob.gpu_ids ?? [])
          .map((gpuId) => (devicesQuery.data ?? []).find((device) => device.id === gpuId))
          .filter(Boolean);
        const workersByGpu = displayJob.gpu_render_mode === FRAME_PER_GPU
          ? Object.fromEntries(selectedDevices.map((device, index) => {
              const workerProgress = Math.min(
                100,
                Math.max(0, displayJob.progress + (index === 0 ? 14 : -9)),
              );
              return [device.uuid, {
                frame: (displayJob.frame_start ?? 1) + index,
                progress: workerProgress,
                sample: Math.round((workerProgress / 100) * 512),
                totalSamples: 512,
              }];
            }))
          : {};
        return {
          frame: displayJob.current_frame ?? displayJob.frame_start ?? null,
          progress: displayJob.progress,
          sample: Math.round((displayJob.progress / 100) * 512),
          totalSamples: 512,
          workersByGpu,
        };
      })()
    : realtime.progressByJob[displayJob.id] ?? null;

  const saveJob = async (jobId, configuration) => {
    if (!isMockMode) return saveJobMutation.mutateAsync({ configuration, jobId });
    const current = jobs.find((job) => job.id === jobId);
    const updated = applyJobConfiguration(current, configuration);
    setMockJobs((currentJobs) => currentJobs.map((job) => job.id === jobId ? updated : job));
    return updated;
  };

  const uploadScene = async (job, file, configuration) => {
    if (!isMockMode) return sceneUploadMutation.mutateAsync({ file, configuration, job });
    if (job.id === "draft") {
      const id = `job-${Date.now()}`;
      const created = applyJobConfiguration(
        {
          ...draftJob,
          id,
          shortId: id.slice(-4).toUpperCase(),
          file: file.name,
          status: "ready",
          version: activeVersion,
          created: "Just now",
          hasArtifacts: false,
        },
        configuration,
      );
      setMockJobs((currentJobs) => [created, ...currentJobs]);
      setSelectedJobId(id);
      return created;
    }
    const current = jobs.find((candidate) => candidate.id === job.id);
    const updated = { ...applyJobConfiguration(current, configuration), file: file.name };
    setMockJobs((currentJobs) => currentJobs.map((candidate) => candidate.id === job.id ? updated : candidate));
    return updated;
  };

  const startRender = async (jobId, configuration) => {
    if (!isMockMode) return startJobMutation.mutateAsync({ configuration, jobId });
    const current = jobs.find((job) => job.id === jobId);
    const updated = {
      ...applyJobConfiguration(current, configuration),
      status: "rendering",
      progress: 12,
      created: "Running now",
      started_at: new Date(Date.now() - 8_000).toISOString(),
      finished_at: null,
    };
    setMockJobs((currentJobs) => currentJobs.map((job) => job.id === jobId ? updated : job));
    setSelectedJobId(jobId);
    return updated;
  };

  const cancelRender = async (jobId) => {
    if (!isMockMode) return cancelJobMutation.mutateAsync(jobId);
    setMockJobs((currentJobs) => currentJobs.map((job) =>
      job.id === jobId
        ? { ...job, status: "cancelled", created: "Cancelled now", finished_at: new Date().toISOString() }
        : job));
    return undefined;
  };

  const rerenderJob = async (jobId) => {
    if (!isMockMode) return rerenderJobMutation.mutateAsync(jobId);
    const source = jobs.find((job) => job.id === jobId);
    const id = `job-${Date.now()}`;
    const rerendered = {
      ...source,
      id,
      shortId: id.slice(-4).toUpperCase(),
      name: `${source.name} rerender`,
      status: "ready",
      progress: 0,
      current_frame: null,
      created: "Just now",
      hasArtifacts: false,
      started_at: null,
      finished_at: null,
    };
    setMockJobs((currentJobs) => [rerendered, ...currentJobs]);
    setSelectedJobId(id);
    return rerendered;
  };

  const deleteJob = async (job) => {
    const jobIndex = jobs.findIndex((candidate) => candidate.id === job.id);
    const remainingJobs = jobs.filter((candidate) => candidate.id !== job.id);
    const replacementJob = remainingJobs[Math.min(jobIndex, remainingJobs.length - 1)] ?? null;
    const nextTotal = Math.max(0, jobTotal - 1);
    const nextPage = Math.min(jobPage, Math.max(1, Math.ceil(nextTotal / JOBS_PER_PAGE)));

    if (isMockMode) {
      setMockJobs((currentJobs) => currentJobs.filter((candidate) => candidate.id !== job.id));
    } else {
      await deleteJobMutation.mutateAsync(job.id);
      queryClient.setQueriesData({ queryKey: ["jobs"] }, (current) => {
        if (!current?.items) return current;
        return {
          ...current,
          items: current.items.filter((candidate) => candidate.id !== job.id),
          total: Math.max(0, current.total - 1),
          pages: Math.ceil(Math.max(0, current.total - 1) / JOBS_PER_PAGE),
        };
      });
      queryClient.removeQueries({ queryKey: ["artifacts", job.id] });
      queryClient.removeQueries({ queryKey: ["frames", job.id] });
      queryClient.removeQueries({ queryKey: ["log-tail", job.id] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    }

    setJobPage(nextPage);
    if (selectedJobId === job.id) setSelectedJobId(replacementJob?.id ?? null);
  };

  const requestJobDeletion = async (job) => {
    if (job.status === "queued" || job.status === "rendering") return;
    setDeleteInspectionError("");
    deleteJobMutation.reset();
    setDeleteInspectionId(job.id);
    try {
      const hasArtifacts = isMockMode
        ? Boolean(job.hasArtifacts)
        : (await queryClient.fetchQuery({
            queryKey: ["artifacts", job.id],
            queryFn: ({ signal }) => artifactsApi.list(job.id, { signal }),
          })).length > 0;
      if (hasArtifacts) {
        setDeleteConfirmationJob(job);
      } else {
        await deleteJob(job);
      }
    } catch (error) {
      setDeleteInspectionError(error instanceof Error ? error.message : "Could not delete job");
    } finally {
      setDeleteInspectionId(null);
    }
  };

  return (
    <div className="app-shell">
      <Header
        activeVersion={activeVersion}
        connectionState={realtime.connectionState}
        gpuCount={(devicesQuery.data ?? []).filter((device) => device.available).length}
        onPairDevice={() => {
          pairingInvitationMutation.reset();
          setPairPanelOpen(true);
          pairingInvitationMutation.mutate();
        }}
        onThemeChange={setTheme}
        onVersions={() => setVersionPanelOpen(true)}
        queuedCount={queueSummary?.queued_count ?? "—"}
        storageWarning={storageWarning}
        theme={theme}
        versionsLoading={versionsQuery.isPending}
      />

      <main>
        <div className="dashboard-grid">
          <JobSetup
            activeVersion={activeVersion}
            devices={devicesQuery.data ?? []}
            job={setupJob}
            key={setupJob.id}
            onCancel={cancelRender}
            onRerender={rerenderJob}
            onSave={saveJob}
            onSceneUpload={uploadScene}
            onStart={startRender}
            onVersions={() => setVersionPanelOpen(true)}
            operationError={
              sceneUploadMutation.error?.message
              ?? saveJobMutation.error?.message
              ?? startJobMutation.error?.message
              ?? cancelJobMutation.error?.message
              ?? rerenderJobMutation.error?.message
              ?? jobsQuery.error?.message
              ?? ""
            }
            operationPending={
              sceneUploadMutation.isPending
              || saveJobMutation.isPending
              || startJobMutation.isPending
              || cancelJobMutation.isPending
              || rerenderJobMutation.isPending
            }
            runner={runner}
            versionsLoading={versionsQuery.isPending}
          />
          <RenderPreview
            devices={devicesQuery.data ?? []}
            isMockMode={isMockMode}
            job={displayJob}
            liveLogs={realtime.logsByJob[displayJob.id] ?? []}
            mockLogLines={mockApi?.logLines}
            sampleProgress={sampleProgress}
          />
          <div className="right-rail">
            <JobQueue
              actionError={deleteInspectionError || deleteJobMutation.error?.message || ""}
              deletingJobId={deleteInspectionId ?? (deleteJobMutation.isPending ? deleteJobMutation.variables : null)}
              jobs={jobs}
              loading={jobsQuery.isFetching}
              onDelete={(job) => void requestJobDeletion(job)}
              onNew={() => {
                setJobPage(1);
                setSelectedJobId("new");
              }}
              onPageChange={setJobPage}
              onSelect={setSelectedJobId}
              page={jobPage}
              pages={jobPages}
              selectedJobId={creatingNewJob ? "new" : selectedJob?.id}
              total={jobTotal}
              now={clockNow}
            />
            <Artifacts job={displayJob} mockApi={mockApi} />
          </div>
          <Metrics
            history={metricsQuery.data?.history ?? []}
            devices={metricDevices}
            processors={processors}
            storages={storages}
          />
        </div>
      </main>

      <footer className="app-footer">
        <span>Render Node · {isMockMode ? "Mock data" : "Persistent jobs"}</span>
        <span>{isMockMode ? "API offline" : jobsQuery.isError ? "API unavailable" : `API ${realtime.connectionState}`} <i /> {isMockMode ? "UI sandbox" : "SQLite + WebSocket"}</span>
      </footer>

      {versionPanelOpen && (
        <VersionPanel
          actionError={
            downloadMutation.error?.message
            ?? installMutation.error?.message
            ?? activateMutation.error?.message
            ?? deleteVersionMutation.error?.message
            ?? ""
          }
          activating={activateMutation.isPending}
          blocked={hasActiveJob}
          blockedReason={!queueSummary ? "Queue status is unavailable. Waiting before switching versions." : undefined}
          catalogQueryFn={versionApi.getOfficialVersions}
          downloadingVersion={downloadMutation.isPending ? downloadMutation.variables : null}
          installingVersion={installMutation.isPending ? installMutation.variables : null}
          deletingVersion={deleteVersionMutation.isPending ? deleteVersionMutation.variables : null}
          onActivate={(version) => activateMutation.mutate(version)}
          onClose={() => setVersionPanelOpen(false)}
          onDownload={(version) => downloadMutation.mutate(version)}
          onDelete={(version) => deleteVersionMutation.mutate(version)}
          onUpload={(file) => uploadMutation.mutate(file)}
          onInstall={(version) => installMutation.mutate(version)}
          uploadError={uploadMutation.error?.message ?? ""}
          uploadingFile={uploadMutation.isPending}
          versions={(versionsQuery.data ?? []).filter((version) => version.installed)}
        />
      )}
      {deleteConfirmationJob && (
        <DeleteJobDialog
          error={deleteJobMutation.error?.message ?? ""}
          job={deleteConfirmationJob}
          onClose={() => {
            if (deleteJobMutation.isPending) return;
            deleteJobMutation.reset();
            setDeleteConfirmationJob(null);
          }}
          onConfirm={async () => {
            await deleteJob(deleteConfirmationJob);
            setDeleteConfirmationJob(null);
          }}
          pending={deleteJobMutation.isPending}
        />
      )}
      {pairPanelOpen && (
        <PairDeviceDialog
          error={pairingInvitationMutation.error}
          invitation={pairingInvitationMutation.data}
          onClose={() => {
            setPairPanelOpen(false);
            pairingInvitationMutation.reset();
          }}
          onRetry={() => {
            pairingInvitationMutation.reset();
            pairingInvitationMutation.mutate();
          }}
          pending={pairingInvitationMutation.isPending}
        />
      )}
    </div>
  );
}
