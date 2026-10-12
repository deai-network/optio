import { useContext, useRef, useState, type ReactNode } from 'react';
import { FileCard } from '@ant-design/x';
import { FileDownloadContext, type FileDownloadHandler } from './FileDownloadContext.js';
import { SlotSpinner } from './spinners/SlotSpinner.js';

// Ant Design X experiment (owner-approved 2026-10-12): an answer's
// `[label](optio-file:relpath)` downloads are listed under the reply as an X
// FileCard.List, each card named after the file (X picks the icon from its
// extension) and described by the link text when that differs, else by its
// folder. X shows no loading state for a plain file, so while a download runs
// the card's icon slot holds the app's spinner, as a running tool row does.
// In the text the link stays a link, showing just the file name
// (AnswerBlock.tsx, FILE_LINK_CLASS).

/** The class of an inline download link; the view's stylesheet turns off
 *  X-markdown's external-link marker for it (a download is not external). */
export const FILE_LINK_CLASS = 'optio-file-link';

export interface FileLink {
  relpath: string;
  label: string;
}

const FILE_LINK = /\[([^\]]*)\]\(optio-file:([^)\s]+)\)/g;

/** The markdown's optio-file: links, each file once, in order of first mention. */
export function fileLinks(markdown: string): FileLink[] {
  const seen = new Set<string>();
  const out: FileLink[] = [];
  for (const m of markdown.matchAll(FILE_LINK)) {
    if (seen.has(m[2])) continue;
    seen.add(m[2]);
    out.push({ relpath: m[2], label: m[1] });
  }
  return out;
}

/** A workdir path's file name (what the link and the card show). */
export function fileName(relpath: string): string {
  return relpath.split('/').pop() || relpath;
}

function describe(relpath: string, label: string): string | undefined {
  const slash = relpath.lastIndexOf('/');
  const folder = slash === -1 ? '' : relpath.slice(0, slash + 1);
  return (label !== '' && label !== fileName(relpath) ? label : folder) || undefined;
}

/** Downloads through the host's handler. `busy`: the files whose download (a
 *  promise the handler returned) still runs; a click on one starts nothing. */
function useDownloads(onDownload: FileDownloadHandler | null) {
  const running = useRef(new Set<string>());
  const [busy, setBusy] = useState<ReadonlySet<string>>(() => new Set());
  const start = (relpath: string) => {
    if (!onDownload || running.current.has(relpath)) return;
    const pending = onDownload(relpath, fileName(relpath));
    if (!(pending instanceof Promise)) return;
    running.current.add(relpath);
    setBusy(new Set(running.current));
    void pending.finally(() => {
      running.current.delete(relpath);
      setBusy(new Set(running.current));
    });
  };
  return { busy, start };
}

/** The answer's files as an X FileCard.List (nothing without a download handler
 *  or without optio-file: links). */
export function AnswerFiles({ text }: { text: string }): ReactNode {
  const onDownload = useContext(FileDownloadContext);
  const { busy, start } = useDownloads(onDownload);
  const links = onDownload ? fileLinks(text) : [];
  if (links.length === 0) return null;
  return (
    <FileCard.List
      size="small"
      items={links.map(({ relpath, label }) => ({
        key: relpath,
        name: fileName(relpath),
        description: describe(relpath, label),
        icon: busy.has(relpath) ? <SlotSpinner localKey={`optio:download:${relpath}`} /> : undefined,
        onClick: () => start(relpath),
      }))}
    />
  );
}
