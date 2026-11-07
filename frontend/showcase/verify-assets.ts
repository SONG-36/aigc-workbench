import {createHash} from 'node:crypto'
import {lstatSync,readFileSync} from 'node:fs'
import {resolve} from 'node:path'
import {availableImageRoles,type ApprovedCaseSpec} from '../src/showcase/manifest.ts'

/** Bind permission to the reviewed bytes; a renamed or replaced file needs a new review. */
export function verifyApprovedAssets(cases: ApprovedCaseSpec[], directory: string): Set<string> {
  const approved = new Set<string>()
  for (const entry of cases) for (const role of availableImageRoles(entry.images)) {
    const image = entry.images[role]!, path = resolve(directory,image.file)
    const stat = lstatSync(path)
    if (!stat.isFile() || stat.isSymbolicLink()) throw new Error('Approved assets must be regular local files: '+image.file)
    const sha256 = createHash('sha256').update(readFileSync(path)).digest('hex')
    if (sha256 !== image.sha256) throw new Error('Approved image SHA-256 mismatch: '+image.file)
    approved.add(image.file)
  }
  return approved
}
