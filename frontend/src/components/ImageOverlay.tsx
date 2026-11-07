import {useEffect, useRef, type ReactNode} from 'react'

export function Overlay({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null)
  const close = useRef(onClose)
  close.current = onClose
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    box.current?.querySelector<HTMLButtonElement>('button')?.focus()
    function keydown(event: KeyboardEvent) {
      if (event.key === 'Escape') { event.preventDefault(); close.current() }
      if (event.key === 'Tab') {
        const nodes = box.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input')
        if (!nodes?.length) return
        const first = nodes[0], last = nodes[nodes.length - 1]
        if (!box.current?.contains(document.activeElement)) { event.preventDefault(); first.focus() }
        else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
      }
    }
    document.addEventListener('keydown',keydown)
    return () => { document.removeEventListener('keydown',keydown); document.body.style.overflow = overflow; previous?.focus({preventScroll:true}) }
  }, [])
  return <div className="overlay" onClick={event => { if (event.target === event.currentTarget) onClose() }}>
    <div ref={box} className="overlay-panel" role="dialog" aria-modal="true" aria-label={title}>
      <div className="section-head preview-header"><h3>{title}</h3><button type="button" onClick={onClose}>关闭</button></div><div className="preview-body">{children}</div></div>
  </div>
}
