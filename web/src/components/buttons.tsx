import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { classNames } from '../lib/classNames';

// Every control is at least 72 px: a fingertip on the Hub. A press shows at
// once (`press`), before Spotify has answered.
const base = 'press flex flex-none items-center justify-center disabled:opacity-30';

type Props = ButtonHTMLAttributes<HTMLButtonElement> & { children: ReactNode };

/** Round 72 px button. `on` lights it up; `ghost` sits on the player's colour. */
export function Chip({ on, ghost, className, ...rest }: Props & { on?: boolean; ghost?: boolean }) {
  const look = ghost
    ? (on ? 'bg-white/18 text-tint-hi' : 'bg-transparent active:bg-white/18')
    : (on ? 'bg-tint-hi text-tint' : 'bg-white/13 active:bg-white/32');
  return <button type="button" className={classNames(base, 'size-18 rounded-full', look, className)} {...rest} />;
}

/** A 72 px tall button with an icon and a word. */
export function Pill({ className, ...rest }: Props) {
  return (
    <button type="button"
            className={classNames(base, 'h-18 gap-2.5 rounded-full bg-white/14 pr-6 pl-5 text-[18px] active:bg-white/32', className)}
            {...rest} />
  );
}

/** Previous and next: a large icon without a background. */
export function Big({ busy, className, ...rest }: Props & { busy?: boolean }) {
  return (
    <button type="button" className={classNames(base, 'size-22 rounded-full active:bg-white/18', busy && 'opacity-50', className)} {...rest} />
  );
}

/** Play: filled in the cover's light tone. `wide` is the bar's version. */
export function Main({ busy, wide, className, ...rest }: Props & { busy?: boolean; wide?: boolean }) {
  return (
    <button type="button"
            className={classNames(base, wide ? 'h-18 w-28 rounded-full' : 'size-26 rounded-full',
                          'bg-tint-hi text-tint active:bg-white', busy && 'opacity-50', className)}
            {...rest} />
  );
}
