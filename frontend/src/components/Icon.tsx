type IconName = 'image' | 'upload' | 'grid' | 'plus' | 'arrow' | 'check' | 'refresh' | 'chevron' | 'download' | 'expand' | 'layers' | 'close'
const paths: Record<IconName, string> = {
  image: 'M4 4h16v16H4z M4 16l5-5 4 4 3-3 4 4 M8 8h.01',
  upload: 'M12 16V3 M7 8l5-5 5 5 M4 15v5h16v-5',
  grid: 'M4 4h6v6H4z M14 4h6v6h-6z M4 14h6v6H4z M14 14h6v6h-6z',
  plus: 'M12 5v14 M5 12h14', arrow: 'M5 12h14 M13 6l6 6-6 6',
  check: 'M5 12l4 4L19 6', refresh: 'M20 7v5h-5 M4 17v-5h5 M5 8a7 7 0 0112-3l3 3 M19 16a7 7 0 01-12 3l-3-3',
  chevron: 'M9 5l7 7-7 7', download: 'M12 3v12 M7 10l5 5 5-5 M4 17v4h16v-4',
  expand: 'M9 3H3v6 M15 3h6v6 M3 15v6h6 M21 15v6h-6',
  layers: 'M12 3l10 5-10 5L2 8z M2 12l10 5 10-5 M2 16l10 5 10-5', close: 'M6 6l12 12 M18 6L6 18',
}
export function Icon({ name, className = '' }: { name: IconName; className?: string }) {
  return <svg className={`icon ${className}`} width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>
}
