/** Temporary browser files for one creation intent. Server Asset IDs remain authoritative. */
export type CachedFiles = {product: File | null; reference: File | null; references?: Record<string, File | null>}
export async function newTaskFiles(id: string, files?: CachedFiles | null) {
  if (!globalThis.indexedDB) throw new Error('本浏览器不能暂存待上传文件。')
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open('aigc-new-task-files', 1)
    request.onupgradeneeded = () => request.result.createObjectStore('files')
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
  try {
    return await new Promise<CachedFiles | undefined>((resolve, reject) => {
      const tx = db.transaction('files', files === undefined ? 'readonly' : 'readwrite')
      const store = tx.objectStore('files')
      const request = files === undefined ? store.get(id) : files === null ? store.delete(id) : store.put(files, id)
      tx.oncomplete = () => resolve(files === undefined ? request.result : undefined)
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })
  } finally { db.close() }
}
