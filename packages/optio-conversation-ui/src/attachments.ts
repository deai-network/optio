/** Shared file-attachment helpers for the conversation views. Engine-neutral. */

export interface Attachment {
  file: File;
  mime: string;
  filename: string;
}

/** Wrap a picked File as an Attachment (mime from the File, filename basename). */
export function toAttachment(file: File): Attachment {
  return {
    file,
    mime: file.type || 'application/octet-stream',
    filename: file.name.split(/[\\/]/).pop() || 'file',
  };
}

/** Read a File as a base64 data URL: "data:<mime>;base64,<…>". */
export function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

/** True if the total size of the attachments is within `cap` bytes. */
export function withinCap(atts: Attachment[], cap: number): boolean {
  return atts.every((a) => a.file.size <= cap);
}
