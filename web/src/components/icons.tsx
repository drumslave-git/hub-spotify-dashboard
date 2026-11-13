import type { ReactNode } from 'react';
import { cn } from '../lib/cn';
import { kind as weatherKind } from '../lib/weather';

interface IconProps { className?: string }

function Svg({ className, children }: IconProps & { children: ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className}>
      {children}
    </svg>
  );
}

export const NoteIcon = (p: IconProps) => (
  <Svg {...p}><path d="M9 18V5l12-2v13" /><circle cx="6" cy="18" r="3" /><circle cx="18" cy="16" r="3" /></Svg>
);
export const ListIcon = (p: IconProps) => (
  <Svg {...p}>
    <line x1="8" y1="6" x2="21" y2="6" /><line x1="8" y1="12" x2="21" y2="12" /><line x1="8" y1="18" x2="21" y2="18" />
    <line x1="3" y1="6" x2="3.01" y2="6" /><line x1="3" y1="12" x2="3.01" y2="12" /><line x1="3" y1="18" x2="3.01" y2="18" />
  </Svg>
);
export const SpeakerIcon = (p: IconProps) => (
  <Svg {...p}><rect x="4" y="2" width="16" height="20" rx="2" /><circle cx="12" cy="14" r="4" /><line x1="12" y1="6" x2="12.01" y2="6" /></Svg>
);
export const PlayIcon = (p: IconProps) => <Svg {...p}><path d="M7 4.5v15l13-7.5z" /></Svg>;
export const PauseIcon = (p: IconProps) => (
  <Svg {...p}><rect x="6" y="4.5" width="4.2" height="15" rx="1" /><rect x="13.8" y="4.5" width="4.2" height="15" rx="1" /></Svg>
);
export const PrevIcon = (p: IconProps) => <Svg {...p}><path d="M19 5.5v13L9.5 12z" /><line x1="6" y1="5.5" x2="6" y2="18.5" /></Svg>;
export const NextIcon = (p: IconProps) => <Svg {...p}><path d="M5 5.5v13l9.5-6.5z" /><line x1="18" y1="5.5" x2="18" y2="18.5" /></Svg>;
export const HeartIcon = (p: IconProps) => (
  <Svg {...p}><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" /></Svg>
);
export const ShuffleIcon = (p: IconProps) => (
  <Svg {...p}>
    <polyline points="16 3 21 3 21 8" /><line x1="4" y1="20" x2="21" y2="3" /><polyline points="21 16 21 21 16 21" />
    <line x1="15" y1="15" x2="21" y2="21" /><line x1="4" y1="4" x2="9" y2="9" />
  </Svg>
);
export const RepeatIcon = ({ one, ...p }: IconProps & { one?: boolean }) => (
  <Svg {...p}>
    <polyline points="17 1 21 5 17 9" /><path d="M3 11V9a4 4 0 0 1 4-4h14" />
    <polyline points="7 23 3 19 7 15" /><path d="M21 13v2a4 4 0 0 1-4 4H3" />
    {one && <path d="M11 10.5l1.2-1v5" />}
  </Svg>
);
export const CloseIcon = (p: IconProps) => <Svg {...p}><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></Svg>;
export const BackIcon = (p: IconProps) => <Svg {...p}><polyline points="15 18 9 12 15 6" /></Svg>;
export const MoreIcon = (p: IconProps) => <Svg {...p}><polyline points="9 18 15 12 9 6" /></Svg>;

// ------------------------------------------------------------- weather
// The big icon moves (`animated`): rays turn, clouds drift, rain falls. A
// part with a transform attribute is wrapped, because a CSS animation would
// replace that attribute.
const sunCls = 'stroke-warm';
const dropCls = 'stroke-cool';
const coverCls = 'fill-[var(--cover,var(--color-bg))]';

export function WeatherIcon({ code, isDay, animated = false }: { code: number; isDay: boolean | number; animated?: boolean }) {
  const a = (cls: string) => (animated ? cls : '');
  const drift = a('animate-wx-drift');
  const rainTop = <path d="M20 16.58A5 5 0 0 0 18 7h-1.26A8 8 0 1 0 4 15.25" />;
  const cloud = (
    <g className={drift}><path className={coverCls} d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z" /></g>
  );
  const lowCloud = (
    <g className={drift}>
      <g transform="translate(0 4.6) scale(.82)">
        <path className={coverCls} vectorEffect="non-scaling-stroke" d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z" />
      </g>
    </g>
  );
  let body: ReactNode;
  switch (weatherKind(code, isDay)) {
    case 'clearDay':
      body = (
        <g className={sunCls}>
          <circle cx="12" cy="12" r="5" />
          <path className={a('origin-[12px_12px] animate-wx-spin')}
                d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
        </g>
      );
      break;
    case 'clearNight':
      body = <path className={a('origin-[12px_12px] animate-wx-rock')} d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />;
      break;
    case 'partlyDay':
      body = (
        <>
          <g className={sunCls}>
            <circle cx="17" cy="7.5" r="3.4" />
            <path className={a('origin-[17px_7.5px] animate-wx-glow')} d="M17 1.2v1.3M22 7.5h1.3M11.9 2.4l.9.9M21.2 3.3l.9-.9" />
          </g>
          {lowCloud}
        </>
      );
      break;
    case 'partlyNight':
      body = (
        <>
          <g className={a('origin-[17px_6px] animate-wx-rock')}>
            <path transform="translate(11.5 0) scale(.5)" vectorEffect="non-scaling-stroke" d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
          </g>
          {lowCloud}
        </>
      );
      break;
    case 'cloudy':
      body = cloud;
      break;
    case 'fog':
      body = (
        <>
          {rainTop}
          <path className={drift} d="M7 19.5h11" />
          <path className={cn(drift, a('[animation-delay:-2.5s]'))} d="M9.5 23h6" />
        </>
      );
      break;
    case 'drizzle':
      body = (
        <>
          {rainTop}
          <path className={cn(dropCls, a('animate-wx-fall'))} d="M8 13v2M16 19v2M12 15v2" />
          <path className={cn(dropCls, a('animate-wx-fall [animation-delay:-0.8s]'))} d="M8 19v2M16 13v2M12 21v2" />
        </>
      );
      break;
    case 'rain':
      body = (
        <>
          {rainTop}
          <path className={cn(dropCls, a('[stroke-dasharray:3.5_2.5] animate-wx-dash'))} d="M16 13v8M8 13v8M12 15v8" />
        </>
      );
      break;
    case 'snow':
      body = (
        <>
          <path d="M20 17.58A5 5 0 0 0 18 8h-1.26A8 8 0 1 0 4 16.25" />
          <path className={cn(dropCls, a('animate-wx-fall-slow'))} d="M8 16h.01M12 18h.01M16 16h.01" />
          <path className={cn(dropCls, a('animate-wx-fall-slow [animation-delay:-1.4s]'))} d="M8 20h.01M12 22h.01M16 20h.01" />
        </>
      );
      break;
    case 'thunder':
      body = (
        <>
          <path d="M19 16.9A5 5 0 0 0 18 7h-1.26a8 8 0 1 0-11.62 9" />
          <path className={cn(sunCls, a('animate-wx-flash'))} d="M13 11l-4 6h6l-4 6" />
        </>
      );
      break;
  }
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"
         strokeLinejoin="round" aria-hidden="true" className="wx size-full overflow-visible">
      {body}
    </svg>
  );
}
