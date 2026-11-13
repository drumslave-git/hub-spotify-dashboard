import { classNames } from '../lib/classNames';
import type { ClockFace } from '../lib/time';

/** The time, "14:05", with a quieter colon and an optional "pm". */
export function Time({ face, id, className, colon }: { face: ClockFace | null; id?: string; className?: string; colon: string }) {
  return (
    <div id={id} className={classNames('font-light whitespace-nowrap', className)}>
      {face ? face.hour : '--'}
      <span className={colon}>:</span>
      {face ? face.minute : '--'}
      {face?.ampm && <span className="ml-[0.3em] text-[0.2em] tracking-normal text-muted">{face.ampm}</span>}
    </div>
  );
}

/** "Friday, 13 November", the weekday stronger. */
export function DateLine({ face, className, tone = 'text-muted', strong = 'text-fg' }: {
  face: ClockFace | null; className?: string; tone?: string; strong?: string;
}) {
  return (
    <div className={classNames('whitespace-nowrap', tone, className)}>
      {face ? <><b className={classNames('font-normal', strong)}>{face.weekday}</b>, {face.day} {face.month}</> : ' '}
    </div>
  );
}
