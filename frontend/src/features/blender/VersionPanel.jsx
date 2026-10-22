import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Icon } from "../../ui/components";
import { useDialogBehavior, useModalEntrance } from "../../ui/dialogHooks";

export default function VersionPanel({
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
  const [catalogOpen, setCatalogOpen] = useState(versions.length === 0);
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
            <p>Activate one installed version for new render jobs.</p>
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
          {versions.length === 0 && (
            <div className="version-list-empty" role="status">
              <strong>No Blender versions installed</strong>
              <span>Choose an official release below, then download, install and activate it.</span>
            </div>
          )}
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

