import { useEffect } from 'react';
import { classNames } from '../lib/classNames';
import { Chip } from './buttons';
import { BackIcon, CloseIcon, MoreIcon, NoteIcon, SpeakerIcon } from './icons';

export interface SheetRow {
  value: string | null; name: string; sub: string;
  art?: string | null; icon: 'note' | 'device'; now?: boolean;
}
export interface SheetState { kind: 'library' | 'devices'; rows: SheetRow[]; page: number; empty: string }

const PER_PAGE = 8;   // two columns of four

/** A list over the screen: what to play, or where to play it. Paged instead
 *  of scrolled, so it does not depend on the device's touch scrolling. */
export function Sheet({ sheet, setPage, pick, close }: {
  sheet: SheetState; setPage: (page: number) => void; pick: (row: SheetRow) => void; close: () => void;
}) {
  const pages = Math.max(1, Math.ceil(sheet.rows.length / PER_PAGE));
  const page = Math.max(0, Math.min(pages - 1, sheet.page));
  const rows = sheet.rows.slice(page * PER_PAGE, (page + 1) * PER_PAGE);

  // Left alone, the list goes away by itself.
  useEffect(() => {
    const timer = setTimeout(close, 40 * 1000);
    return () => clearTimeout(timer);
  }, [sheet, close]);

  return (
    <div className="absolute inset-0 z-5 flex flex-col bg-panel px-6 py-5 text-fg">
      <div className="flex h-18 items-center gap-4 pl-2 text-[28px] font-medium">
        <span className="flex-1">{sheet.kind === 'devices' ? 'Play on' : 'Play something'}</span>
        <Chip aria-label="Close" onClick={close}><CloseIcon className="size-7" /></Chip>
      </div>
      <div className="mt-3 grid min-h-0 flex-1 auto-rows-[84px] grid-cols-2 content-start gap-x-4 gap-y-2">
        {rows.map((row, i) => (
          <button key={page * PER_PAGE + i} type="button" onClick={() => pick(row)}
                  className="press flex h-21 w-full items-center justify-start gap-4 rounded-2xl px-3 text-left active:bg-white/10">
            <span className="flex size-16 flex-none items-center justify-center rounded-[10px] bg-raised bg-cover bg-center bg-no-repeat text-muted"
                  style={row.art ? { backgroundImage: `url("${row.art}")` } : undefined}>
              {!row.art && (row.icon === 'device'
                ? <SpeakerIcon className="size-7 stroke-[1.8]" />
                : <NoteIcon className="size-7 stroke-[1.8]" />)}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[20px]">{row.name}</span>
              <span className={classNames('mt-[3px] block truncate text-[15px]', row.now ? 'text-warm' : 'text-muted')}>{row.sub}</span>
            </span>
          </button>
        ))}
        {!rows.length && <div className="col-span-full px-3 py-4 text-[20px] leading-[1.4] text-muted">{sheet.empty}</div>}
      </div>
      <div className={classNames('mt-3 flex items-center gap-4 text-[18px] text-muted', pages < 2 && 'invisible')}>
        <button type="button" aria-label="Previous page" disabled={page === 0} onClick={() => setPage(page - 1)}
                className="press flex h-18 flex-1 items-center justify-center rounded-[20px] bg-raised active:bg-[#2c313a] disabled:opacity-30">
          <BackIcon className="size-7" />
        </button>
        <span className="min-w-[5em] text-center">{page + 1} / {pages}</span>
        <button type="button" aria-label="Next page" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}
                className="press flex h-18 flex-1 items-center justify-center rounded-[20px] bg-raised active:bg-[#2c313a] disabled:opacity-30">
          <MoreIcon className="size-7" />
        </button>
      </div>
    </div>
  );
}
