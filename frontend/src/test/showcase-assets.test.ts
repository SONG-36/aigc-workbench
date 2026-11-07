// @vitest-environment node
import {createHash} from 'node:crypto'
import {mkdtempSync,rmSync,writeFileSync,symlinkSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {expect,it} from 'vitest'
import {parseManifest} from '../showcase/manifest'
import {verifyApprovedAssets} from '../../showcase/verify-assets'

it('binds each image approval to exact bytes and refuses changed, missing or symlinked assets',()=>{
  const directory=mkdtempSync(join(tmpdir(),'showcase-assets-'))
  try {
    const bytes=Buffer.from('TEST_ONLY build guard fixture'),file='test-only.png'
    const sha256=createHash('sha256').update(bytes).digest('hex')
    const image={file,sha256,authorizationStatus:'APPROVED_FOR_PUBLIC_SHOWCASE',approvalRecord:'TEST_ONLY',alt:'TEST_ONLY',caption:'TEST_ONLY'}
    const cases=parseManifest({cases:[{slug:'test-only',title:'TEST_ONLY',productCategory:'TEST_ONLY',description:'TEST_ONLY',displayOrder:0,
      approvalRecord:'TEST_ONLY',clearance:{confidentiality:true,publication:true},resultReview:'HUMAN_PASSED',images:{product:image,result:image}}]})
    expect(()=>verifyApprovedAssets(cases,directory)).toThrow()
    writeFileSync(join(directory,file),bytes)
    expect([...verifyApprovedAssets(cases,directory)]).toEqual([file])
    writeFileSync(join(directory,file),'TEST_ONLY replacement');expect(()=>verifyApprovedAssets(cases,directory)).toThrow('SHA-256 mismatch')
    rmSync(join(directory,file));writeFileSync(join(directory,'other.png'),bytes);symlinkSync(join(directory,'other.png'),join(directory,file))
    expect(()=>verifyApprovedAssets(cases,directory)).toThrow('regular local files')
  } finally {rmSync(directory,{recursive:true,force:true})}
})
