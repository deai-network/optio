import { createContext } from 'react';

/** Engine view -> Markdown renderer seam: turns an `optio-file:` sentinel link
 *  into an actual download. Null when no engine view provides one (e.g.
 *  conversation-scripter's reuse of AnswerBlock) — the renderer then degrades
 *  to a plain link. */
export type FileDownloadHandler = (relpath: string, filename: string) => void;

export const FileDownloadContext = createContext<FileDownloadHandler | null>(null);

/** Trigger a browser download of in-memory bytes. */
export function blobDownload(bytes: BlobPart, mime: string, filename: string): void {
  const url = URL.createObjectURL(new Blob([bytes], { type: mime || 'application/octet-stream' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
