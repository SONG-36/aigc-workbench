import {readFile,readdir,lstat} from 'node:fs/promises'
import {createHash} from 'node:crypto'
import {fileURLToPath} from 'node:url'
import {resolve,dirname,relative,extname} from 'node:path'
import assert from 'node:assert/strict'

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..')
const directory=resolve(root,'dist-showcase')
const manifest=JSON.parse(await readFile(resolve(root,'showcase/cases.json'),'utf8'))
const report=JSON.parse(await readFile(resolve(directory,'showcase-checks.json'),'utf8'))
assert.equal(report.kind,'READ_ONLY_STATIC_SHOWCASE')
assert.equal(report.internalApplicationModules,0)
assert.equal(report.publicCases,manifest.cases.length)
const hash=data=>createHash('sha256').update(data).digest('hex')
const allowedImages=new Set([hash(await readFile(resolve(root,'showcase/favicon.svg')))])
for(const entry of manifest.cases)for(const image of Object.values(entry.images))
  allowedImages.add(hash(await readFile(resolve(root,'src/showcase/assets/approved',image.file))))
let files=0,images=0
async function inspect(folder) {
  for(const name of await readdir(folder)) {
    const path=resolve(folder,name),stat=await lstat(path)
    assert(!stat.isSymbolicLink(),'No symlinks in the public artifact')
    if(stat.isDirectory()){await inspect(path);continue}
    const rel=relative(directory,path),extension=extname(name)
    assert(rel==='index.html'||rel==='showcase-checks.json'||/^assets\/[a-zA-Z0-9_-]+\.(js|css|svg|png|jpe?g|webp)$/.test(rel),'Unexpected public file: '+rel)
    const data=await readFile(path)
    if(['.svg','.png','.jpg','.jpeg','.webp'].includes(extension)) {
      assert(allowedImages.has(hash(data)),'Image is absent from the approved-source inventory: '+rel);images++
    } else {
      assert(!/OPENAI_API_KEY|DASHSCOPE_API_KEY|api\.openai\.com|\.runtime|workbench\.sqlite|\/api\/|task_id|run_id|snapshot_id|\/workspace\/|TEST_ONLY|[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/i.test(data.toString()),'Internal or test material found in '+rel)
      if(extension==='.css')assert(!/@import|url\s*\(/i.test(data.toString()),'Public styles must not load additional resources')
    }
    files++
  }
}
await inspect(directory)
const html=await readFile(resolve(directory,'index.html'),'utf8')
assert(html.includes("connect-src 'none'"));assert(html.includes("form-action 'none'"))
assert(!/https?:\/\//.test(html),'No third-party runtime resources')
console.log(`SHOWCASE_ISOLATION_PASS: ${files} static files, ${images} approved image assets, ${report.publicCases} public cases; no internal app modules or private artifacts.`)
