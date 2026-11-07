import {defineConfig,type Plugin} from 'vite'
import react from '@vitejs/plugin-react'
import {readFileSync} from 'node:fs'
import {dirname,resolve,relative,basename} from 'node:path'
import {fileURLToPath} from 'node:url'
import {parseManifest} from './src/showcase/manifest.ts'
import {verifyApprovedAssets} from './showcase/verify-assets.ts'

const frontend = dirname(fileURLToPath(import.meta.url))
const source = resolve(frontend,'src/showcase')
const approvedDirectory = resolve(frontend,'.showcase-assets/approved')
const assetModule = '\0showcase-approved-assets'
const shared = new Set(['components/Icon.tsx','components/ImageOverlay.tsx','styles/tokens.css','styles/base.css','styles/preview.css']
  .map(name=>resolve(frontend,'src',name)))

function publicOnly():Plugin {
  return {
    name:'showcase-public-only',
    resolveId(id) { if(id==='virtual:showcase-assets')return assetModule },
    load(id) {
      if(id!==assetModule)return
      const cases = parseManifest(JSON.parse(readFileSync(resolve(frontend,'showcase/cases.json'),'utf8')))
      const files = [...verifyApprovedAssets(cases,approvedDirectory)]
      return files.map((file,index)=>`import image${index} from ${JSON.stringify(resolve(approvedDirectory,file)+'?url')};`).join('\n')
        + '\nexport default {'+files.map((file,index)=>`${JSON.stringify(file)}:image${index}`).join(',')+'};'
    },
    generateBundle() {
      const cases = parseManifest(JSON.parse(readFileSync(resolve(frontend,'showcase/cases.json'),'utf8')))
      const approved = verifyApprovedAssets(cases,approvedDirectory)
      const modules:string[] = []
      for(const id of this.getModuleIds()) {
        if(id.startsWith('\0'))continue
        const file=id.split('?')[0]
        if(file.includes('/node_modules/'))continue
        if(file.startsWith(approvedDirectory+'/')) {
          if(!approved.has(basename(file)))this.error('Unapproved public image import: '+basename(file))
          modules.push('approved-image:'+basename(file));continue
        }
        const own = file.startsWith(source+'/') || file.startsWith(resolve(frontend,'showcase')+'/')
        if(!own&&!shared.has(file))this.error('Showcase must not import internal application modules: '+relative(frontend,file))
        if(file.startsWith(source+'/assets/'))
          this.error('Unapproved public image import: '+basename(file))
        modules.push(relative(frontend,file))
      }
      this.emitFile({type:'asset',fileName:'showcase-checks.json',source:JSON.stringify({
        kind:'READ_ONLY_STATIC_SHOWCASE',publicCases:cases.length,externalConnections:'DISABLED_BY_CSP',
        internalApplicationModules:0,modules:[...new Set(modules)].sort(),
      },null,2)})
      // Explicit allowlist: never copy a public directory or attach server functions.
      for(const fileName of ['_headers','404.html','robots.txt'])
        this.emitFile({type:'asset',fileName,source:readFileSync(resolve(frontend,'showcase',fileName),'utf8')})
    },
  }
}

export default defineConfig({
  root:resolve(frontend,'showcase'),base:'./',envDir:false,envPrefix:'SHOWCASE_PUBLIC_',publicDir:false,
  plugins:[react(),publicOnly()],
  build:{outDir:resolve(frontend,'dist-showcase'),emptyOutDir:true,sourcemap:false,assetsInlineLimit:0,modulePreload:false},
  // No API proxy. Production verification uses a plain static-file server.
  server:{host:'127.0.0.1',port:8040,strictPort:true},
  preview:{host:'127.0.0.1',port:8040,strictPort:true},
})
