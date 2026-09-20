/**
 * API client for session file uploads (add-session-file-upload).
 *
 * Mirrors the backend endpoints in api/session_uploads.py:
 *   - POST   /sessions/{sid}/uploads         — upload single file (multipart)
 *   - GET    /sessions/{sid}/uploads         — list uploaded file metadata
 *   - DELETE /sessions/{sid}/uploads/{fid}   — delete one uploaded file
 *
 * Uses the shared apiClient singleton (U2: no new axios/fetch). 401 and
 * business codes are handled by the apiClient interceptor.
 */

import type { UploadedFileRef } from '@/types/api'
import apiClient from './client'

/** POST /sessions/{sid}/uploads — upload a single file (multipart/form-data).
 *  Backend validates extension (txt/md/csv/xlsx/docx; xmind forbidden) and
 *  size (≤ settings.upload_max_size_mb, default 1MB). Returns 413 on oversize,
 *  415 on bad extension, 404 if session unknown. */
export const uploadFile = (sessionId: string, file: File): Promise<UploadedFileRef> => {
  const form = new FormData()
  form.append('file', file)
  return apiClient
    .post<UploadedFileRef>(`/sessions/${sessionId}/uploads`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then(r => r.data)
}

/** POST /sessions/{sid}/uploads/batch — upload multiple files in one request
 *  (multipart, repeated field name 'files'). Atomic: if any file fails
 *  validation (extension/size), the backend rolls back already-persisted
 *  files + their session_state.uploads entries. Returns
 *  `{ files: UploadedFileRef[] }`.
 *
 *  Differences vs `uploadFile`:
 *  - `uploadFile` hits `/uploads` (single-file endpoint, kept for backward
 *    compat — used by MessageInputBar / RefineChat).
 *  - `uploadFilesBatch` hits `/uploads/batch` (multi-file endpoint, ≤5 files
 *    per request, atomic rollback on partial failure).
 *
 *  Images are NOT accepted here — frontend MUST convert via
 *  `/vision/convert` first, then wrap the returned text as a `.txt` File and
 *  pass that through `uploadFilesBatch` (same paradigm as `uploadFile`). */
export const uploadFilesBatch = (
  sessionId: string,
  files: File[]
): Promise<{ files: UploadedFileRef[] }> => {
  const form = new FormData()
  for (const f of files) form.append('files', f)
  return apiClient
    .post<{ files: UploadedFileRef[] }>(
      `/sessions/${sessionId}/uploads/batch`,
      form,
      { headers: { 'Content-Type': 'multipart/form-data' } }
    )
    .then(r => r.data)
}

/** GET /sessions/{sid}/uploads — list metadata of files uploaded to a session. */
export const listUploads = (sessionId: string): Promise<{ files: UploadedFileRef[] }> =>
  apiClient
    .get<{ files: UploadedFileRef[] }>(`/sessions/${sessionId}/uploads`)
    .then(r => r.data)

/** DELETE /sessions/{sid}/uploads/{fileId} — delete one uploaded file
 *  (disk + session_state entry). Returns 204 on success (void). */
export const deleteUpload = (sessionId: string, fileId: string): Promise<void> =>
  apiClient
    .delete(`/sessions/${sessionId}/uploads/${fileId}`)
    .then(() => undefined)
