/**
 * API client for the vision image-to-text convert service.
 *
 * Mirrors backend api/vision.py:
 *   - POST /vision/convert — accept an image (multipart), return text + filename
 *
 * Stateless OCR-like service: no session coupling. The caller (FileUploader)
 * wraps the returned text as a new File (e.g. `foo.txt`) and uploads it via the
 * normal session upload flow — same paradigm as a user-selected .txt file.
 */

import apiClient from './client'

export interface ConvertResponse {
  text: string
  suggested_filename: string
  source_name: string
  size_bytes: number
}

/** POST /vision/convert — convert an image to text via the VISION slot model.
 *  Backend validates extension (png/jpg/jpeg/gif/webp only) and size
 *  (≤ settings.upload_max_size_mb, default 1MB). Returns 413 on oversize,
 *  415 on non-image extension, 502 if vision model call fails.
 *
 *  Uses a longer timeout (120s) than the apiClient default (30s) because
 *  vision model calls routinely take 30-60s on dense images. The apiClient
 *  default would otherwise abort the request mid-flight even though the
 *  backend is still working — the frontend would show "timeout of 30000ms
 *  exceeded" while the backend logs success. */
export const convertImageToText = (file: File): Promise<ConvertResponse> => {
  const form = new FormData()
  form.append('file', file)
  return apiClient
    .post<ConvertResponse>('/vision/convert', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    })
    .then(r => r.data)
}
