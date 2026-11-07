import manifest from '../../showcase/cases.json'
// Unit tests use URLs only. They never read or upload commercial image bytes.
export default Object.fromEntries(manifest.cases.flatMap(entry=>Object.values(entry.images)
  .map(image=>[image.file,`/test-only-public/${image.file}`])))
