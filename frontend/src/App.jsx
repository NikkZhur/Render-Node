import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { artifactsApi, blenderApi, devicesApi, jobsApi, systemApi } from "./api";
import { getLogLevel } from "./logPresentation";
import { useRenderEvents } from "./realtime";
import { useUiStore } from "./store";

const FRAMES_PER_PAGE = 50;
const LOG_TAB_TRANSITION_FALLBACK_MS = 180;
const LOG_PANEL_TRANSITION_FALLBACK_MS = 280;
const COMPUTE_DEVICES = ["OptiX", "CUDA", "CPU"];
const FRAME_MODES = ["single", "range", "all"];
const RENDER_ENGINES = ["Cycles", "Eevee", "Workbench"];
const ENGINE_API_VALUES = {
  Cycles: "CYCLES",
  Eevee: "BLENDER_EEVEE",
  Workbench: "BLENDER_WORKBENCH",
};
const emptyListQuery = async () => [];
const draftJob = {
  id: "draft",
  shortId: "NEW",
  name: "New render",
  file: "Choose a .blend or ZIP",
  status: "created",
  progress: 0,
  frame: "1–240",
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

function Icon({ name, size = 18 }) {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": true,
  };

  const paths = {
    chevron: <path d="m9 18 6-6-6-6" />,
    down: <path d="m6 9 6 6 6-6" />,
    play: <path d="m8 5 11 7-11 7Z" />,
    stop: <rect x="7" y="7" width="10" height="10" rx="1" />,
    layers: (
      <>
        <path d="m12 3-9 5 9 5 9-5-9-5Z" />
        <path d="m3 12 9 5 9-5M3 16l9 5 9-5" />
      </>
    ),
    gpu: (
      <>
        <rect x="5" y="6" width="14" height="12" rx="2" />
        <path d="M9 10h6v4H9zM9 2v4m6-4v4M9 18v4m6-4v4M2 9h3m-3 6h3m14-6h3m-3 6h3" />
      </>
    ),
    disk: (
      <>
        <ellipse cx="12" cy="5" rx="8" ry="3" />
        <path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7" />
      </>
    ),
    clock: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
      </>
    ),
    sun: (
      <>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2m0 16v2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M2 12h2m16 0h2M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42" />
      </>
    ),
    moon: <path d="M20 15.2A8.4 8.4 0 0 1 8.8 4 8.5 8.5 0 1 0 20 15.2Z" />,
    file: (
      <>
        <path d="M6 2h8l4 4v16H6z" />
        <path d="M14 2v5h5" />
      </>
    ),
    image: (
      <>
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <circle cx="9" cy="10" r="2" />
        <path d="m4 18 5-5 3 3 3-3 5 5" />
      </>
    ),
    download: (
      <>
        <path d="M12 3v12m-5-5 5 5 5-5" />
        <path d="M5 21h14" />
      </>
    ),
    upload: (
      <>
        <path d="M12 21V9m-5 5 5-5 5 5" />
        <path d="M5 3h14" />
      </>
    ),
    settings: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z" />
      </>
    ),
    close: <path d="m6 6 12 12M18 6 6 18" />,
    trash: (
      <>
        <path d="M4 7h16M9 7V4h6v3m3 0-1 14H7L6 7" />
        <path d="M10 11v6m4-6v6" />
      </>
    ),
    check: <path d="m5 12 4 4L19 6" />,
    expand: <path d="M8 3H3v5m13-5h5v5M8 21H3v-5m13 5h5v-5" />,
    cube: (
      <>
        <path d="m12 2 9 5-9 5-9-5 9-5Z" />
        <path d="m3 7 9 5v10l-9-5V7Zm18 0-9 5v10l9-5V7Z" />
      </>
    ),
  };

  return <svg {...common}>{paths[name]}</svg>;
}

function StatusBadge({ status }) {
  const labels = {
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

function Header({ activeVersion, connectionState, gpuCount, onThemeChange, onVersions, queuedCount, storageWarning, theme }) {
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
            <strong>12m</strong> avg. frame
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
            {connectionState === "reconnecting" ? "Reconnecting" : connectionState === "connecting" ? "Connecting" : "Node online"}
            <span className="latency">WS</span>
          </div>
          <button className="version-button" onClick={onVersions} type="button">
            <span className="version-icon">
              <Icon name="cube" size={16} />
            </span>
            <span>
              Blender {activeVersion ?? "—"}
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

function SectionHeading({ eyebrow, title, action }) {
  return (
    <div className="section-heading">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h2>{title}</h2>
      </div>
      {action}
    </div>
  );
}

function DropdownField({ label, onChange, options, value }) {
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
  isMockMode,
  job,
  onStart,
  onCancel,
  onSceneUpload,
  uploadError,
  uploading,
}) {
  const [engine, setEngine] = useState(job.engine ?? "Cycles");
  const [device, setDevice] = useState(job.device ?? "OptiX");
  const [frameMode, setFrameMode] = useState(job.frame_mode?.toLowerCase() ?? "range");
  const [selectedGpus, setSelectedGpus] = useState(job.gpu_ids ?? null);
  const [fileName, setFileName] = useState(job.file);
  const fileInput = useRef(null);
  const frameStartInput = useRef(null);
  const frameEndInput = useRef(null);
  const isActive = job.status === "queued" || job.status === "rendering";
  const activeGpuIds = selectedGpus ?? devices.map((gpu) => gpu.id);
  const computeDevices = devices.length > 0 ? COMPUTE_DEVICES : ["CPU"];
  const effectiveDevice = devices.length > 0 ? device : "CPU";

  const toggleGpu = (gpuId) => {
    setSelectedGpus((current) => {
      const currentSelection = current ?? devices.map((gpu) => gpu.id);
      return (
        currentSelection.includes(gpuId)
          ? currentSelection.filter((id) => id !== gpuId)
          : [...currentSelection, gpuId]
      );
    });
  };

  return (
    <section className="panel setup-panel">
      <SectionHeading eyebrow="New render" title="Job setup" />

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
          onChange={(event) => {
            const nextFile = event.target.files?.[0];
            if (!nextFile) return;
            setFileName(nextFile.name);
            const frameStart = Number.parseInt(frameStartInput.current?.value ?? "1", 10);
            const frameEnd = Number.parseInt(frameEndInput.current?.value ?? "240", 10);
            onSceneUpload(nextFile, {
              name: nextFile.name.replace(/\.(blend|zip)$/i, ""),
              blender_version: activeVersion,
              engine: ENGINE_API_VALUES[engine],
              device: effectiveDevice.toUpperCase(),
              gpu_ids: effectiveDevice === "CPU" ? [] : activeGpuIds,
              frame_mode: frameMode.toUpperCase(),
              frame_start: frameMode === "all" ? null : frameStart,
              frame_end: frameMode === "range" ? frameEnd : null,
            });
          }}
        />
        <button
          className="file-drop"
          onClick={() => fileInput.current?.click()}
          type="button"
        >
          <span className="file-icon">
            <Icon name="file" size={20} />
          </span>
          <span className="file-copy">
            <strong>{fileName}</strong>
            <small>{uploading ? "Uploading…" : job.status === "ready" ? "Ready to render" : "Select a scene to upload"}</small>
          </span>
          <span className="replace-file">Replace</span>
        </button>
      </div>

      <div className="two-column-fields">
        <DropdownField label="Render engine" onChange={setEngine} options={RENDER_ENGINES} value={engine} />
        <DropdownField label="Compute device" onChange={setDevice} options={computeDevices} value={effectiveDevice} />
      </div>

      <div className="field-group">
        <div className="field-label-row">
          <label>Frames</label>
          <span>SCENE 1–240</span>
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
              key={mode}
              onClick={() => setFrameMode(mode)}
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
              <input defaultValue={job.frame_start ?? 1} inputMode="numeric" ref={frameStartInput} />
            </label>
            <span className="range-line" />
            <label>
              <span>End</span>
              <input defaultValue={job.frame_end ?? 240} inputMode="numeric" ref={frameEndInput} />
            </label>
          </div>
        )}
        {frameMode === "single" && (
          <div className="frame-range single-frame">
            <label>
              <span>Frame</span>
              <input defaultValue={job.frame_start ?? 1} inputMode="numeric" ref={frameStartInput} />
            </label>
          </div>
        )}
      </div>

      <div className="field-group gpu-field">
        <div className="field-label-row">
          <label>GPU allocation</label>
          <span>{activeGpuIds.length} SELECTED</span>
        </div>
        <div className="gpu-list">
          {devices.length === 0 && <span className="gpu-empty">No NVIDIA GPUs discovered · CPU mode</span>}
          {devices.map((gpu) => {
            const selected = activeGpuIds.includes(gpu.id);
            return (
              <button
                className={`gpu-option ${selected ? "selected" : ""}`}
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

      <div className="active-runtime">
        <span>
          <Icon name="cube" size={15} />
          Runtime
        </span>
        <strong>Blender {activeVersion}</strong>
      </div>

      <button
        aria-busy={uploading}
        className={`primary-action ${isActive ? "danger-action" : ""}`}
        disabled={uploading || (!isMockMode && job.status !== "ready" && !isActive)}
        onClick={isActive ? onCancel : onStart}
        type="button"
      >
        <Icon name={isActive ? "stop" : "play"} size={18} />
        {job.status === "rendering" ? "Cancel render" : job.status === "queued" ? "Cancel job" : "Start render"}
      </button>
      {uploadError && <p className="manual-upload-error" role="alert">{uploadError}</p>}
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

function RenderPreview({ isMockMode, job, liveLogs = [], mockLogLines = [] }) {
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
  const shownProgress = isRendering ? job.progress : job.status === "completed" ? 100 : 0;
  const currentFrame = latestFrame?.frame ?? job.current_frame ?? job.frame_start ?? 1;
  const frameNumber = String(currentFrame).padStart(3, "0");
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
  const currentTask = isRendering
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
            <StatusBadge status={job.status} />
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
              <small>Start a ready job to run Blender on this node.</small>
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

        <div className="render-progress-block">
          <div className="render-progress-copy">
            <div>
              <span className="eyebrow">Current task</span>
              <strong>{currentTask}</strong>
            </div>
            <div className="progress-stats">
              <span><small>FRAME</small>{isRendering ? currentFrame : "—"}</span>
              <span><small>STATE</small>{job.status}</span>
              <b>{shownProgress}%</b>
            </div>
          </div>
          <div className="progress-track" aria-label={`Render progress ${shownProgress}%`}>
            <span style={{ width: `${shownProgress}%` }} />
          </div>
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

function JobQueue({ jobs, selectedJobId, onSelect }) {
  return (
    <section className="panel queue-panel">
      <SectionHeading
        eyebrow="Workspace"
        title="Jobs"
        action={<span className="queue-count">{jobs.length}</span>}
      />
      <div className="job-list">
        {jobs.map((job) => (
          <button
            className={`job-row ${selectedJobId === job.id ? "selected" : ""}`}
            key={job.id}
            onClick={() => onSelect(job.id)}
            type="button"
          >
            <span className="job-status-rail" data-status={job.status} />
            <span className="job-main">
              <span className="job-title-line">
                <strong>{job.name}</strong>
                <small>#{job.shortId}</small>
              </span>
              <span className="job-detail">{job.engine} · {job.device} · {job.frame}</span>
              <span className="job-bottom-line">
                <StatusBadge status={job.status} />
                <small>{job.created}</small>
              </span>
              {job.status === "rendering" && (
                <span className="job-mini-progress"><i style={{ width: `${job.progress}%` }} /></span>
              )}
            </span>
            <Icon name="chevron" size={15} />
          </button>
        ))}
      </div>
      <button className="text-action" type="button">
        View complete history <Icon name="chevron" size={14} />
      </button>
    </section>
  );
}

function makeMetricHistory(current, seed) {
  const offsets = [-13, -5, -9, 4, -2, 9, 3, 0];
  return offsets.map((offset, index) => (
    Math.max(0, Math.min(100, current + offset * (0.55 + seed * 0.08) + ((index + seed) % 3) * 2))
  ));
}

function MetricChart({ chartId, history, tone }) {
  const width = 240;
  const height = 88;
  const points = history.map((value, index) => ({
    x: (index / (history.length - 1)) * width,
    y: 8 + ((100 - value) / 100) * (height - 18),
  }));
  const linePath = points.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(" ");
  const areaPath = `${linePath} L ${width} ${height} L 0 ${height} Z`;
  const color = `var(--${tone})`;

  return (
    <svg aria-hidden="true" className="metric-chart" preserveAspectRatio="none" viewBox={`0 0 ${width} ${height}`}>
      <defs>
        <linearGradient id={chartId} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.38" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path className="metric-area" d={areaPath} fill={`url(#${chartId})`} />
      <path className="metric-line" d={linePath} stroke={color} />
    </svg>
  );
}

function MetricCard({ chartId, history, label, percent, tone, value }) {
  return (
    <article className="metric-item">
      <div aria-label={`${label}: ${value}`} className="metric-ring" style={{ "--metric": percent, "--tone": `var(--${tone})` }}>
        <div className="metric-ring-content">
          <span>{label}</span>
          <strong>{value}</strong>
        </div>
      </div>
      <MetricChart chartId={chartId} history={history} tone={tone} />
    </article>
  );
}

function ResourceRow({ detail, kind, metrics, name }) {
  const resourceKey = `${kind}-${name}`.replace(/[^a-z0-9]+/gi, "-").toLowerCase();
  return (
    <div className="resource-row">
      <div className="resource-identity">
        <span>{kind}</span>
        <strong>{name}</strong>
        <small>{detail}</small>
      </div>
      <div className="resource-metrics">
        {metrics.map((metric, index) => (
          <MetricCard chartId={`${resourceKey}-${index}`} key={metric.label} {...metric} />
        ))}
      </div>
    </div>
  );
}

function Metrics({ devices, processors, storages }) {
  const gpuRows = devices.map((gpu) => ({
    detail: `${gpu.memoryTotal} GB VRAM`,
    kind: `GPU ${String(gpu.id + 1).padStart(2, "0")}`,
    name: gpu.name.replace("NVIDIA ", ""),
    metrics: [
      { label: "Load", value: `${gpu.utilization}%`, percent: gpu.utilization, tone: "blue", history: makeMetricHistory(gpu.utilization, gpu.id + 1) },
      { label: "VRAM", value: `${gpu.memoryUsed} GB`, percent: (gpu.memoryUsed / gpu.memoryTotal) * 100, tone: "violet", history: makeMetricHistory((gpu.memoryUsed / gpu.memoryTotal) * 100, gpu.id + 2) },
      { label: "Temp", value: `${gpu.temperature}°`, percent: gpu.temperature, tone: gpu.temperature < 80 ? "green" : "red", history: makeMetricHistory(gpu.temperature, gpu.id + 3) },
    ],
  }));
  const cpuRows = processors.map((cpu) => ({
    detail: `${cpu.cores} cores`,
    kind: `CPU ${String(cpu.id + 1).padStart(2, "0")}`,
    name: cpu.name,
    metrics: [
      { label: "Load", value: `${cpu.utilization}%`, percent: cpu.utilization, tone: "green", history: makeMetricHistory(cpu.utilization, cpu.id + 4) },
      { label: "Memory", value: `${cpu.memoryUsed} GB`, percent: (cpu.memoryUsed / cpu.memoryTotal) * 100, tone: "orange", history: makeMetricHistory((cpu.memoryUsed / cpu.memoryTotal) * 100, cpu.id + 5) },
      { label: "Temp", value: `${cpu.temperature}°`, percent: cpu.temperature, tone: cpu.temperature < 80 ? "green" : "red", history: makeMetricHistory(cpu.temperature, cpu.id + 6) },
    ],
  }));
  const storageRows = storages.map((storage) => {
    const usedPercent = ((storage.totalGb - storage.freeGb) / storage.totalGb) * 100;
    const freePercent = (storage.freeGb / storage.totalGb) * 100;
    const capacity = storage.totalGb >= 1000 ? `${storage.totalGb / 1000} TB` : `${storage.totalGb} GB`;

    return {
      detail: `${storage.mountPoint} · ${storage.freeGb} GB free of ${capacity}`,
      kind: `STORAGE ${String(storage.id + 1).padStart(2, "0")}`,
      name: storage.name,
      metrics: [
        { label: "Used", value: `${Math.round(usedPercent)}%`, percent: usedPercent, tone: freePercent < 10 ? "red" : freePercent < 20 ? "orange" : "blue", history: makeMetricHistory(usedPercent, storage.id + 7) },
        { label: "Read", value: `${storage.readMbps} MB/s`, percent: (storage.readMbps / storage.maxThroughputMbps) * 100, tone: "green", history: makeMetricHistory((storage.readMbps / storage.maxThroughputMbps) * 100, storage.id + 8) },
        { label: "Write", value: `${storage.writeMbps} MB/s`, percent: (storage.writeMbps / storage.maxThroughputMbps) * 100, tone: "violet", history: makeMetricHistory((storage.writeMbps / storage.maxThroughputMbps) * 100, storage.id + 9) },
      ],
    };
  });
  const resourceRows = [...gpuRows, ...cpuRows, ...storageRows];

  return (
    <section aria-label="Resource metrics" className="metrics-strip">
      <div className="metrics-scroll" style={{ "--visible-resource-rows": Math.min(2, resourceRows.length || 1) }}>
        {resourceRows.map((resource) => (
          <ResourceRow key={`${resource.kind}-${resource.name}`} {...resource} />
        ))}
        {resourceRows.length === 0 && (
          <div className="metrics-empty">No hardware metrics available</div>
        )}
      </div>
    </section>
  );
}

function useModalEntrance() {
  const [entered, setEntered] = useState(false);

  useEffect(() => {
    const animationFrame = window.requestAnimationFrame(() => setEntered(true));
    return () => window.cancelAnimationFrame(animationFrame);
  }, []);

  return entered;
}

function useDialogBehavior(onClose, initialFocusRef) {
  const dialogRef = useRef(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    const previousFocus = document.activeElement;
    const focusFrame = window.requestAnimationFrame(() => initialFocusRef.current?.focus());

    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = [...(dialogRef.current?.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ) ?? [])].filter((element) => element.tabIndex >= 0);
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }

      const first = focusable[0];
      const last = focusable.at(-1);
      if (event.shiftKey && (document.activeElement === first || !dialogRef.current?.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !dialogRef.current?.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
      }
    };

    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", onKeyDown);

    return () => {
      window.cancelAnimationFrame(focusFrame);
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", onKeyDown);
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus();
    };
  }, [initialFocusRef]);

  return dialogRef;
}

function FrameSequencePanel({ jobId, mockApi, onClose }) {
  const isMockMode = mockApi !== null;
  const closeButton = useRef(null);
  const dialogRef = useDialogBehavior(onClose, closeButton);
  const entered = useModalEntrance();
  const [page, setPage] = useState(1);
  const framesQuery = useQuery({
    queryKey: ["frames", jobId, page, FRAMES_PER_PAGE],
    queryFn: ({ signal }) => isMockMode
      ? mockApi.getFrames({ page, pageSize: FRAMES_PER_PAGE })
      : artifactsApi.frames(jobId, { page, pageSize: FRAMES_PER_PAGE, signal }),
  });
  const frames = framesQuery.data?.items ?? [];
  const totalFrames = framesQuery.data?.total ?? (mockApi?.frameCount ?? 0);
  const pageCount = Math.max(1, framesQuery.data?.pages ?? Math.ceil(totalFrames / FRAMES_PER_PAGE));
  const pageStart = (page - 1) * FRAMES_PER_PAGE;
  const pageEnd = Math.min(pageStart + FRAMES_PER_PAGE, totalFrames);

  return (
    <div className={`modal-backdrop frames-backdrop ${entered ? "is-entered" : ""}`} role="presentation" onMouseDown={onClose}>
      <section
        aria-describedby="frames-description"
        aria-labelledby="frames-title"
        aria-modal="true"
        className="frames-modal"
        onMouseDown={(event) => event.stopPropagation()}
        ref={dialogRef}
        role="dialog"
      >
        <div className="frames-modal-header">
          <div>
            <span className="eyebrow">Render sequence</span>
            <h2 id="frames-title">Frames {totalFrames ? `1–${totalFrames}` : "—"}</h2>
            <p id="frames-description">{totalFrames} {isMockMode ? "PNG files" : "files"} · {pageCount} pages</p>
          </div>
          <div className="frames-modal-actions">
            <a className="sequence-download" download href={isMockMode ? "#" : artifactsApi.framesZipUrl(jobId)} aria-label="Download all frames as ZIP" role="button">
              <Icon name="download" size={16} /> Download ZIP
            </a>
            <button ref={closeButton} className="icon-button" onClick={onClose} type="button" aria-label="Close frame sequence">
              <Icon name="close" />
            </button>
          </div>
        </div>

        <ol aria-busy={framesQuery.isPending} className="frame-list" key={page}>
          {framesQuery.isPending && <li className="frame-list-loading">Loading frames…</li>}
          {frames.map((frame) => (
            <li className="frame-row" key={frame.frame}>
              <strong>{frame.name ?? frame.filename}</strong>
              <a download href={frame.original_url ?? "#"} aria-label={`Download ${frame.name ?? frame.filename}`} role="button">
                <Icon name="download" size={16} />
              </a>
            </li>
          ))}
        </ol>

        <div className="frame-pagination">
          <span>{pageStart + 1}–{pageEnd} of {totalFrames}</span>
          <div>
            <button disabled={page === 1 || framesQuery.isFetching} onClick={() => setPage((current) => current - 1)} type="button">Previous</button>
            <strong>Page {page} of {pageCount}</strong>
            <button disabled={page === pageCount || framesQuery.isFetching} onClick={() => setPage((current) => current + 1)} type="button">Next</button>
          </div>
        </div>
      </section>
    </div>
  );
}

function Artifacts({ job, mockApi }) {
  const isMockMode = mockApi !== null;
  const [framesOpen, setFramesOpen] = useState(false);
  const enabled = !isMockMode && job.id !== "draft";
  const framesQuery = useQuery({
    queryKey: ["frames", job.id, "summary"],
    queryFn: ({ signal }) => artifactsApi.frames(job.id, { page: 1, pageSize: 1, signal }),
    enabled,
  });
  const artifactsQuery = useQuery({
    queryKey: ["artifacts", job.id],
    queryFn: ({ signal }) => artifactsApi.list(job.id, { signal }),
    enabled,
  });
  const frameCount = mockApi?.frameCount ?? framesQuery.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(frameCount / FRAMES_PER_PAGE));
  const logArtifact = isMockMode
    ? { filename: "blender.log", size_bytes: 284 * 1024 }
    : artifactsQuery.data?.find((artifact) => artifact.kind === "blender_log");

  return (
    <>
      <section className="panel artifacts-panel">
        <SectionHeading eyebrow="Selected job" title="Artifacts" />
        <div className="artifact-list">
          {frameCount > 0 && <button
            aria-haspopup="dialog"
            aria-label={`Open frame sequence, ${frameCount} frames`}
            className="artifact-row artifact-sequence-trigger"
            onClick={() => setFramesOpen(true)}
            type="button"
          >
            <span className="artifact-kind">SEQ</span>
            <span><strong>frames_0001–{String(frameCount).padStart(4, "0")}</strong><small>{frameCount} frames · {pageCount} pages</small></span>
            <span className="artifact-open-icon"><Icon name="chevron" size={16} /></span>
          </button>}
          {logArtifact && <div className="artifact-row">
            <span className="artifact-kind">LOG</span>
            <span><strong>{logArtifact.filename}</strong><small>{Math.max(1, Math.round(logArtifact.size_bytes / 1024))} KB</small></span>
            <a download href={isMockMode ? "#" : artifactsApi.logUrl(job.id)} aria-label="Download blender.log" role="button"><Icon name="download" size={16} /></a>
          </div>}
          {!frameCount && !logArtifact && <p className="artifacts-empty">No artifacts yet</p>}
        </div>
      </section>
      {framesOpen && (
        <FrameSequencePanel
          jobId={job.id}
          mockApi={mockApi}
          onClose={() => setFramesOpen(false)}
        />
      )}
    </>
  );
}

function VersionPanel({
  versions,
  blocked,
  onClose,
  onDownload,
  onUpload,
  onInstall,
  onActivate,
  onDelete,
  downloadingVersion,
  uploadingFile,
  uploadError,
  installingVersion,
  activating,
  deletingVersion,
  catalogQueryFn,
  actionError,
}) {
  const entered = useModalEntrance();
  const [catalogOpen, setCatalogOpen] = useState(false);
  const closeButton = useRef(null);
  const dialogRef = useDialogBehavior(onClose, closeButton);
  const installerInput = useRef(null);
  const catalogQuery = useQuery({
    queryKey: ["official-versions"],
    queryFn: catalogQueryFn,
    enabled: catalogOpen,
    staleTime: 5 * 60 * 1000,
  });

  return (
    <div className={`modal-backdrop ${entered ? "is-entered" : ""}`} role="presentation" onMouseDown={onClose}>
      <section
        aria-labelledby="versions-title"
        aria-modal="true"
        className={`version-modal ${catalogOpen ? "catalog-open" : ""}`}
        onMouseDown={(event) => event.stopPropagation()}
        ref={dialogRef}
        role="dialog"
      >
        <div className="modal-header">
          <div>
            <span className="eyebrow">Runtime manager</span>
            <h2 id="versions-title">Blender versions</h2>
            <p>One installed version is active for every new render job.</p>
          </div>
          <button
            aria-label="Close version manager"
            className="icon-button"
            onClick={onClose}
            ref={closeButton}
            type="button"
          >
            <Icon name="close" />
          </button>
        </div>

        {blocked && (
          <div className="modal-notice">
            <span className="notice-pulse" />
            Finish or cancel the active render before switching versions.
          </div>
        )}

        <div className="version-list">
          {versions.map((version) => (
            <article className={`version-row ${version.active ? "active" : ""}`} key={version.version}>
              <span className="version-cube"><Icon name="cube" size={20} /></span>
              <div className="version-info">
                <div>
                  <strong>Blender {version.version}</strong>
                  <span className={`channel-tag ${version.channel === "LTS" ? "lts" : ""}`}>{version.channel}</span>
                </div>
                <small>
                  {version.source === "bundled"
                    ? "Included in image"
                    : version.source === "manual"
                      ? "Uploaded manually"
                      : "Downloaded from blender.org"}
                </small>
              </div>
              <div className="version-flags">
                {version.supported ? <span className="supported"><Icon name="check" size={12} /> Supported</span> : <span>Untested</span>}
              </div>
              <div className="version-action">
                {version.active ? (
                  <span className="active-label"><span /> Active</span>
                ) : version.installed ? (
                  <>
                    <button
                      disabled={blocked || activating || Boolean(deletingVersion)}
                      onClick={() => onActivate(version.version)}
                      type="button"
                    >
                      {activating ? "Switching…" : "Make active"}
                    </button>
                    {version.source !== "bundled" && (
                      <button
                        aria-label={`Delete Blender ${version.version}`}
                        className="delete-version-button"
                        disabled={blocked || Boolean(deletingVersion)}
                        onClick={() => {
                          if (window.confirm(`Delete Blender ${version.version} and its installer archive?`)) {
                            onDelete(version.version);
                          }
                        }}
                        title={`Delete Blender ${version.version}`}
                        type="button"
                      >
                        <Icon name="trash" size={15} />
                      </button>
                    )}
                  </>
                ) : null}
              </div>
            </article>
          ))}
        </div>

        <div className="version-catalog">
          <div className="version-catalog-actions">
            <button
              aria-expanded={catalogOpen}
              className="version-catalog-toggle"
              onClick={() => setCatalogOpen((open) => !open)}
              type="button"
            >
              <span>
                <strong>Choose other versions</strong>
                <small>Loaded from the official Blender archive</small>
              </span>
              <Icon name="chevron" size={16} />
            </button>
            <input
              accept=".tar.xz,.tar.bz2"
              className="sr-only"
              onChange={(event) => {
                const [file] = event.target.files;
                if (file) {
                  setCatalogOpen(true);
                  onUpload(file);
                }
                event.target.value = "";
              }}
              ref={installerInput}
              tabIndex={-1}
              type="file"
            />
            <button
              className="manual-version-upload"
              disabled={uploadingFile}
              onClick={() => installerInput.current?.click()}
              type="button"
            >
              <Icon name="upload" size={16} />
              {uploadingFile ? "Uploading…" : "Upload installer"}
            </button>
          </div>

          {(uploadError || actionError) && (
            <p className="manual-upload-error">{uploadError || actionError}</p>
          )}

          {catalogOpen && (
            <div aria-label="Available Blender versions" className="version-catalog-content">
              {catalogQuery.isPending && <p className="version-catalog-status">Loading official versions…</p>}
              {catalogQuery.isError && (
                <div className="version-catalog-status error">
                  <span>Could not load the official archive.</span>
                  <button onClick={() => catalogQuery.refetch()} type="button">Retry</button>
                </div>
              )}
              {catalogQuery.isSuccess && catalogQuery.data.length === 0 && (
                <p className="version-catalog-status">No other release branches are available.</p>
              )}
              {catalogQuery.data?.map((version) => (
                <article className="version-row catalog-version-row" key={version.version}>
                  <span className="version-cube"><Icon name="cube" size={20} /></span>
                  <div className="version-info">
                    <div>
                      <strong>Blender {version.version}</strong>
                      <span className="channel-tag">{version.channel}</span>
                    </div>
                    <small>
                      {version.source === "manual"
                        ? `${version.fileName}${version.size ? ` · ${version.size}` : ""} · ready to install`
                        : version.downloaded
                          ? "Downloaded · ready to install"
                          : "Official release branch"}
                    </small>
                  </div>
                  <div className="version-flags">
                    {version.supported ? (
                      <span className="supported"><Icon name="check" size={12} /> Supported</span>
                    ) : (
                      <span>Untested</span>
                    )}
                  </div>
                  <div className="version-action">
                    {version.downloaded ? (
                      <>
                        <button
                          disabled={Boolean(installingVersion) || Boolean(deletingVersion)}
                          onClick={() => onInstall(version.version)}
                          type="button"
                        >
                          {installingVersion === version.version ? "Installing…" : "Install"}
                        </button>
                        <button
                          aria-label={`Delete Blender ${version.version} download`}
                          className="delete-version-button"
                          disabled={Boolean(installingVersion) || Boolean(deletingVersion)}
                          onClick={() => {
                            if (window.confirm(`Delete the Blender ${version.version} installer archive?`)) {
                              onDelete(version.version);
                            }
                          }}
                          title={`Delete Blender ${version.version} download`}
                          type="button"
                        >
                          <Icon name="trash" size={15} />
                        </button>
                      </>
                    ) : (
                      <button
                        disabled={Boolean(downloadingVersion)}
                        onClick={() => onDownload(version.version)}
                        type="button"
                      >
                        {downloadingVersion === version.version ? "Downloading…" : "Download"}
                      </button>
                    )}
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>

        <div className="modal-footer">
          <span>Downloads are verified with the official SHA-256 before installation.</span>
          <a href="https://download.blender.org/release/" target="_blank" rel="noreferrer">Official archive <Icon name="chevron" size={13} /></a>
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
  const [theme, setTheme] = useState(getInitialTheme);
  const realtime = useRenderEvents(queryClient, !isMockMode);

  const jobsQuery = useQuery({
    queryKey: ["jobs"],
    queryFn: jobsApi.list,
    enabled: !isMockMode,
  });
  const versionApi = mockApi ?? blenderApi;
  const versionsQuery = useQuery({ queryKey: ["versions"], queryFn: versionApi.getVersions });
  const devicesQuery = useQuery({
    queryKey: ["devices"],
    queryFn: isMockMode ? mockApi.getDevices : devicesApi.list,
  });
  const processorsQuery = useQuery({
    queryKey: ["processors"],
    queryFn: mockApi?.getProcessors ?? emptyListQuery,
    enabled: isMockMode,
  });
  const storagesQuery = useQuery({
    queryKey: ["storages"],
    queryFn: mockApi?.getStorages ?? emptyListQuery,
    enabled: isMockMode,
  });
  const metricsQuery = useQuery({
    queryKey: ["metrics"],
    queryFn: systemApi.metrics,
    enabled: !isMockMode,
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

  const activeVersion = versionsQuery.data?.find((version) => version.active)?.version ?? "5.2.0";
  const jobs = isMockMode ? mockJobs : (jobsQuery.data ?? []);
  const updateJobCache = (updatedJob) => {
    queryClient.setQueryData(["jobs"], (current = []) => [
      updatedJob,
      ...current.filter((job) => job.id !== updatedJob.id),
    ]);
  };
  const sceneUploadMutation = useMutation({
    mutationFn: async ({ file, configuration }) => {
      const createdJob = await jobsApi.create(configuration);
      try {
        return await jobsApi.upload(createdJob.id, file);
      } catch (error) {
        await jobsApi.delete(createdJob.id).catch(() => undefined);
        throw error;
      }
    },
    onSuccess: (uploadedJob) => {
      updateJobCache(uploadedJob);
      setSelectedJobId(uploadedJob.id);
    },
  });
  const startJobMutation = useMutation({
    mutationFn: jobsApi.start,
    onSuccess: updateJobCache,
  });
  const cancelJobMutation = useMutation({
    mutationFn: jobsApi.cancel,
    onSuccess: updateJobCache,
  });
  const liveJob = isMockMode
    ? (jobs[0] ?? draftJob)
    : (jobs.find((job) => job.status === "rendering" || job.status === "queued")
      ?? jobs.find((job) => job.status === "ready")
      ?? jobs[0]
      ?? draftJob);
  const selectedJob = jobs.find((job) => job.id === selectedJobId) ?? liveJob;
  const isRendering = jobs.some((job) => job.status === "rendering");
  const hasActiveJob = jobs.some((job) => job.status === "queued" || job.status === "rendering");
  const metricDevices = isMockMode ? (devicesQuery.data ?? []) : (metricsQuery.data?.devices ?? []);
  const processors = isMockMode ? (processorsQuery.data ?? []) : (metricsQuery.data?.processors ?? []);
  const storages = isMockMode ? (storagesQuery.data ?? []) : (metricsQuery.data?.storages ?? []);
  const storageWarning = storages.find(
    (storage) => storage.status === "low_space"
      || storage.freeGb < 50
      || storage.freeGb / storage.totalGb < 0.1,
  );

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
          };
        }),
      );
    }, 650);
    return () => window.clearInterval(interval);
  }, [isMockMode, isRendering]);

  const displayJob = selectedJob;

  const startRender = () => {
    if (!isMockMode) {
      if (liveJob.status === "ready") startJobMutation.mutate(liveJob.id);
      return;
    }
    setSelectedJobId(liveJob.id);
    setMockJobs((currentJobs) => {
      const nextReadyJob = currentJobs.find(
        (job) => job.id !== liveJob.id && job.status === "ready",
      );
      return currentJobs.map((job) =>
        job.id === liveJob.id
          ? { ...job, status: "rendering", progress: 12, created: "Running now", version: activeVersion }
          : job.id === nextReadyJob?.id
            ? { ...job, status: "queued" }
            : job,
      );
    });
  };

  const cancelRender = () => {
    if (!isMockMode) {
      if (liveJob.status === "queued" || liveJob.status === "rendering") {
        cancelJobMutation.mutate(liveJob.id);
      }
      return;
    }
    setMockJobs((currentJobs) => {
      const queuedJob = currentJobs.find((job) => job.status === "queued");
      return currentJobs.map((job) =>
        job.id === liveJob.id && job.status === "rendering"
          ? { ...job, status: "cancelled", created: "Cancelled now" }
          : job.id === queuedJob?.id
            ? { ...job, status: "ready" }
            : job,
      );
    });
  };

  return (
    <div className="app-shell">
      <Header
        activeVersion={activeVersion}
        connectionState={realtime.connectionState}
        gpuCount={(devicesQuery.data ?? []).filter((device) => device.available).length}
        onThemeChange={setTheme}
        onVersions={() => setVersionPanelOpen(true)}
        queuedCount={jobs.filter((job) => job.status === "queued").length}
        storageWarning={storageWarning}
        theme={theme}
      />

      <main>
        <div className="dashboard-grid">
          <JobSetup
            activeVersion={activeVersion}
            devices={devicesQuery.data ?? []}
            isMockMode={isMockMode}
            job={liveJob}
            key={liveJob.id}
            onCancel={cancelRender}
            onSceneUpload={(file, configuration) => {
              if (!isMockMode) sceneUploadMutation.mutate({ file, configuration });
            }}
            onStart={startRender}
            uploadError={
              sceneUploadMutation.error?.message
              ?? startJobMutation.error?.message
              ?? cancelJobMutation.error?.message
              ?? jobsQuery.error?.message
              ?? ""
            }
            uploading={sceneUploadMutation.isPending}
          />
          <RenderPreview
            isMockMode={isMockMode}
            job={displayJob}
            liveLogs={realtime.logsByJob[displayJob.id] ?? []}
            mockLogLines={mockApi?.logLines}
          />
          <div className="right-rail">
            <JobQueue jobs={jobs} onSelect={setSelectedJobId} selectedJobId={selectedJobId} />
            <Artifacts job={displayJob} mockApi={mockApi} />
          </div>
          <Metrics
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
    </div>
  );
}
