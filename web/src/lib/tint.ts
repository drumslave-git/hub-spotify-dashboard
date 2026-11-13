// The colour of the cover. Material's colour library picks the cover's most
// suitable colour (the way Android themes itself from a wallpaper); the page
// uses a deep tone of it as the background and a light tone for the main
// buttons. A grey cover, or one that can't be read, leaves the neutral grey.
import { argbFromRgb, Hct, hexFromArgb, QuantizerCelebi, Score, TonalPalette } from '@material/material-color-utilities';
import { useEffect, useState } from 'react';
import { heartbeat } from './heartbeat';

export interface Tint { tint: string; hi: string }
export const NEUTRAL: Tint = { tint: '#24272d', hi: '#eceef2' };

const tints = new Map<string, Tint>();

function coverTint(img: HTMLImageElement): Tint {
  const size = 64;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const g = canvas.getContext('2d')!;
  g.drawImage(img, 0, 0, size, size);
  const data = g.getImageData(0, 0, size, size).data, pixels: number[] = [];
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] > 250) pixels.push(argbFromRgb(data[i], data[i + 1], data[i + 2]));
  }
  const best = Score.score(QuantizerCelebi.quantize(pixels, 128), { fallbackColorARGB: 0 })[0];
  if (!best) return NEUTRAL;
  const hct = Hct.fromInt(best);
  const tones = TonalPalette.fromHueAndChroma(hct.hue, Math.min(hct.chroma, 36));
  return { tint: hexFromArgb(tones.tone(20)), hi: hexFromArgb(tones.tone(94)) };
}

/** The tint for a cover, set on the page as --color-tint and --color-tint-hi. */
export function useTint(url: string | null | undefined): Tint {
  const [tint, setTint] = useState<Tint>(NEUTRAL);
  useEffect(() => {
    if (!url) { setTint(NEUTRAL); return; }
    const known = tints.get(url);
    if (known) { setTint(known); return; }
    let current = true;
    const img = new Image();
    img.crossOrigin = 'anonymous';   // Spotify's image server allows reading the pixels
    img.onload = () => {
      let t = NEUTRAL;
      try { t = coverTint(img); } catch (e) { heartbeat('tint: ' + (e as Error)?.message); }
      tints.set(url, t);
      if (current) setTint(t);
    };
    img.onerror = () => { tints.set(url, NEUTRAL); if (current) setTint(NEUTRAL); };
    img.src = url;
    return () => { current = false; };
  }, [url]);
  useEffect(() => {
    const root = document.documentElement.style;
    root.setProperty('--color-tint', tint.tint);
    root.setProperty('--color-tint-hi', tint.hi);
  }, [tint]);
  return tint;
}
