import { useRef, useState, type PointerEvent, type Ref } from 'react';
import type { Player } from '../types';
import { classNames } from '../lib/classNames';
import type { Action, Note } from '../lib/player';
import { position } from '../lib/player';
import { clock } from '../lib/time';
import { Big, Chip, Main, Pill } from './buttons';
import {
  HeartIcon, ListIcon, NextIcon, NoteIcon, PauseIcon, PlayIcon, PrevIcon, RepeatIcon, ShuffleIcon, SpeakerIcon,
} from './icons';

/** The album cover, or a note when there is none. */
export function Art({ url, className, iconClass, ref }: { url?: string | null; className?: string; iconClass: string; ref?: Ref<HTMLDivElement> }) {
  return (
    <div ref={ref}
         className={classNames('flex items-center justify-center bg-white/10 bg-cover bg-center bg-no-repeat text-white/35', className)}
         style={url ? { backgroundImage: `url("${url}")` } : undefined}>
      {!url && <NoteIcon className={iconClass} />}
    </div>
  );
}

export type OpenSheet = (kind: 'library' | 'devices') => void;

/** With nothing playing the player waits as one bar in the last cover's colour. */
export function Bar({ player, title, artist, note, busy, art, command, open, className }: {
  player: Player | null; title: string; artist: string; note: Note; busy: boolean; art: string | null;
  command: (a: Action) => void; open: OpenSheet; className?: string;
}) {
  const ready = !!player?.connected;
  const sub = note.alert ? note.text : [artist, note.text].filter(Boolean).join(' · ');
  return (
    <div id="bar" className={classNames('flex h-26 flex-none items-center gap-4 rounded-[26px] p-4 text-white text-shadow-none', className)}>
      <Art url={art} className="size-18 flex-none rounded-[10px]" iconClass="size-8 stroke-[1.6]" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[21px] font-semibold">{title}</div>
        <div className={classNames('mt-1 truncate text-[16px]', note.alert ? 'text-warm' : 'text-white/75')}>{sub}</div>
      </div>
      <Pill disabled={!ready} onClick={() => open('library')}><ListIcon className="size-6" />Browse</Pill>
      <Pill disabled={!ready} onClick={() => open('devices')}><SpeakerIcon className="size-6" />Speakers</Pill>
      <Main wide aria-label="Play" busy={busy} disabled={!ready || (!player?.active && !player?.local_ready)}
            onClick={() => command('toggle')}>
        <PlayIcon className="size-[30px] fill-current stroke-none" />
      </Main>
    </div>
  );
}

/** The seek bar: a thick track with a handle, in a strip tall enough to land
 *  a finger on. Tap anywhere on it, or drag the handle. */
function Seek({ player, offset, command }: { player: Player; offset: number; command: (a: Action, v: number) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState<number | null>(null);   // where the finger is, 0..1
  const duration = player.active ? player.duration_ms ?? 0 : 0;
  const pos = !duration ? 0 : dragging == null ? position(player, offset) : dragging * duration;
  const part = duration ? 100 * pos / duration : 0;

  const at = (e: PointerEvent) => {
    const r = box.current!.getBoundingClientRect();
    return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
  };
  const end = (e: PointerEvent, apply: boolean) => {
    if (dragging == null) return;
    const p = at(e);
    setDragging(null);
    if (apply && duration) command('seek', Math.round(p * duration));
  };
  return (
    <div>
      <div ref={box} className="relative flex h-12 touch-none items-center"
           onPointerDown={(e) => {
             if (!duration) return;
             setDragging(at(e));
             try { box.current!.setPointerCapture(e.pointerId); } catch { /* finger already gone */ }
           }}
           onPointerMove={(e) => { if (dragging != null) setDragging(at(e)); }}
           onPointerUp={(e) => end(e, true)}
           onPointerCancel={(e) => end(e, false)}>
        <div className="h-3.5 flex-1 overflow-hidden rounded-[7px] bg-white/20">
          <i className="block h-full bg-tint-hi" style={{ width: `${part}%` }} />
        </div>
        <div className={classNames('absolute top-1/2 left-0 -mt-4 -ml-4 size-8 rounded-full bg-tint-hi shadow-[0_2px_10px_rgba(0,0,0,0.35)] transition-[scale] duration-80',
                           dragging != null && 'scale-135')}
             style={{ left: `${part}%` }} />
      </div>
      <div className="flex justify-between text-[15px] text-white/75">
        <span>{clock(duration ? pos : 0)}</span><span>{clock(duration)}</span>
      </div>
    </div>
  );
}

/** The player's panel while music plays: cover, title, seek bar, controls. */
export function PlayerPanel({ player, note, busy, offset, command, open, shown, canvas, glow, art }: {
  player: Player | null; note: Note; busy: boolean; offset: number;
  command: (a: Action, v?: number) => void; open: OpenSheet; shown: boolean;
  canvas: Ref<HTMLCanvasElement>; glow: Ref<HTMLDivElement>; art: Ref<HTMLDivElement>;
}) {
  const p = player ?? {};
  const ready = !!p.connected, active = ready && !!p.active;
  const show = active ? p : ready ? p.last ?? null : null;
  // Each part rises into place once the panel has grown under it.
  const rise = classNames('relative z-1',
    shown
      ? 'translate-y-0 opacity-100 [transition:opacity_0.4s_0.45s,translate_0.5s_cubic-bezier(0.2,0.7,0.3,1)_0.45s]'
      : 'translate-y-3.5 opacity-0 [transition:opacity_0.25s,translate_0.25s] motion-reduce:translate-y-0');
  return (
    <div id="side" className="relative flex min-w-0 flex-col justify-between p-7 text-white">
      <canvas ref={canvas} width={624} height={170}
              className={classNames('pointer-events-none absolute bottom-0 left-0 z-0 h-[170px] w-[624px]',
                             shown ? 'opacity-100 [transition:opacity_0.4s_0.45s]' : 'opacity-0 [transition:opacity_0.25s]')} />
      <div className={classNames(rise, 'flex h-66 gap-6')}>
        <div className="relative size-66 flex-none">
          <div ref={glow} className="absolute -inset-12 rounded-full bg-[radial-gradient(closest-side,var(--color-tint-hi),transparent)] opacity-0 will-change-[transform,opacity]" />
          <Art ref={art} url={show?.art} iconClass="size-20 stroke-[1.2]"
               className="relative size-66 flex-none rounded-xl shadow-[0_18px_40px_rgba(0,0,0,0.4)] will-change-transform" />
        </div>
        <div className="flex min-w-0 flex-1 flex-col items-start">
          <div className={classNames('max-w-full truncate text-[15px]', note.alert ? 'text-warm' : 'text-white/70')}>{note.text}</div>
          <div className="mt-2.5 line-clamp-3 text-[31px] leading-[1.12] font-semibold tracking-[-0.015em] wrap-anywhere">{show?.title ?? ''}</div>
          <div className="mt-2 line-clamp-2 text-[20px] text-white/75">{show?.artist ?? ''}</div>
          <Chip className="mt-auto" aria-label="Like" on={active && !!p.liked}
                disabled={!active || p.liked == null} onClick={() => command('like')}>
            <HeartIcon className={classNames('size-7', active && p.liked && 'fill-current')} />
          </Chip>
        </div>
      </div>

      <div className={rise}>
        <Seek player={p} offset={offset} command={command} />
      </div>

      <div className={classNames(rise, 'flex items-center justify-between px-2')}>
        <Chip ghost aria-label="Shuffle" on={active && !!p.shuffle} disabled={!active} onClick={() => command('shuffle')}>
          <ShuffleIcon className="size-7" />
        </Chip>
        <Big aria-label="Previous" busy={busy} disabled={!active} onClick={() => command('prev')}>
          <PrevIcon className="size-[42px] fill-current stroke-[2.4]" />
        </Big>
        <Main aria-label="Play or pause" busy={busy} disabled={!ready || (!active && !p.local_ready)} onClick={() => command('toggle')}>
          {active && p.playing
            ? <PauseIcon className="size-11 fill-current stroke-none" />
            : <PlayIcon className="size-11 fill-current stroke-none" />}
        </Main>
        <Big aria-label="Next" busy={busy} disabled={!active} onClick={() => command('next')}>
          <NextIcon className="size-[42px] fill-current stroke-[2.4]" />
        </Big>
        <Chip ghost aria-label="Repeat" on={active && !!p.repeat && p.repeat !== 'off'} disabled={!active} onClick={() => command('repeat')}>
          <RepeatIcon className="size-7" one={active && p.repeat === 'track'} />
        </Chip>
      </div>

      <div className={classNames(rise, 'flex items-center gap-3')}>
        <div className="min-w-0 flex-1 text-[15px] leading-[1.35] text-white/65">
          {active && p.next && (
            <>Up next<b className="block truncate text-[17px] font-normal text-white">
              {p.next.title}{p.next.artist ? ' · ' + p.next.artist : ''}
            </b></>
          )}
        </div>
        <Pill className="max-w-[220px]" disabled={!ready} onClick={() => open('library')}>
          <ListIcon className="size-6" /><span className="truncate">Browse</span>
        </Pill>
        <Pill className="max-w-[220px]" disabled={!ready} onClick={() => open('devices')}>
          <SpeakerIcon className="size-6" /><span className="truncate">{(active && p.device) || 'Speakers'}</span>
        </Pill>
      </div>
    </div>
  );
}
