import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { artifactsApi } from "../../api";
import { FRAMES_PER_PAGE, frameSequencePresentation } from "../../artifactPresentation";
import { Icon, SectionHeading } from "../../ui/components";
import { useDialogBehavior, useModalEntrance } from "../../ui/dialogHooks";

function FrameSequencePanel({ jobId, mockApi, onClose, sequenceTitle }) {
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
            <h2 id="frames-title">{sequenceTitle}</h2>
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

export default function Artifacts({ job, mockApi }) {
  const isMockMode = mockApi !== null;
  const [framesOpen, setFramesOpen] = useState(false);
  const enabled = !isMockMode && job.id !== "draft";
  const framesQuery = useQuery({
    queryKey: ["frames", job.id, "summary"],
    queryFn: ({ signal }) => artifactsApi.frames(job.id, { page: 1, pageSize: 1, signal }),
    enabled,
  });
  const artifactsQuery = useQuery({
    queryKey: ["artifacts", job.id, "blender_log"],
    queryFn: ({ signal }) => artifactsApi.list(job.id, { signal, kind: "blender_log" }),
    enabled,
  });
  const hasMockArtifacts = isMockMode && job.id !== "draft" && job.hasArtifacts !== false;
  const frameCount = hasMockArtifacts ? mockApi.frameCount : (framesQuery.data?.total ?? 0);
  const pageCount = Math.max(1, Math.ceil(frameCount / FRAMES_PER_PAGE));
  const sequence = frameSequencePresentation(job, framesQuery.data, frameCount);
  const logArtifact = hasMockArtifacts
    ? { filename: "blender.log", size_bytes: 284 * 1024 }
    : artifactsQuery.data?.find((artifact) => artifact.kind === "blender_log");

  return (
    <>
      <section className="panel artifacts-panel">
        <SectionHeading eyebrow="Selected job" title="Artifacts" />
        <div className="artifact-list">
          {frameCount > 0 && <button
            aria-haspopup="dialog"
            aria-label={`Open frame sequence, ${sequence.countText}`}
            className="artifact-row artifact-sequence-trigger"
            onClick={() => setFramesOpen(true)}
            type="button"
          >
            <span className="artifact-kind">SEQ</span>
            <span><strong>{sequence.name}</strong><small>{sequence.countText} · {pageCount} pages</small></span>
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
          sequenceTitle={sequence.title}
        />
      )}
    </>
  );
}

