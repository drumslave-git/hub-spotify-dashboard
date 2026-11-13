// The music, seen: while music plays here a soft spectrum rises behind the
// controls, the cover breathes with the bass and light glows behind it, all
// in the cover's colour. About 30 frames a second, and only while it plays.
import { useEffect, useRef, type RefObject } from 'react';
import { analyser, ctx } from './audio';
import { diag } from './diag';
import { rgb } from './sky';
import { NEUTRAL } from './tint';

const BARS = 40, W = 624, H = 170;

function levelsNow(t: number, bins: Uint8Array<ArrayBuffer> | null): number[] {
  const out: number[] = [];
  if (!analyser || !ctx || !bins) {   // demo page: a made-up song at 120 beats a minute
    const kick = Math.exp(-((t / 500) % 1) * 5);
    for (let j = 0; j < BARS; j++) {
      const v = 0.5 + 0.22 * Math.sin(t / 390 + j * 0.45) + 0.14 * Math.sin(t / 160 + j * 1.9);
      out.push(Math.max(0, v * (1 - j / BARS * 0.55) * (j < 6 ? 0.55 + kick * 0.6 : 0.85)));
    }
    return out;
  }
  analyser.getByteFrequencyData(bins);
  const hz = ctx.sampleRate / analyser.fftSize;
  for (let i = 0; i < BARS; i++) {
    // Bands spaced the way we hear: as many bars for 50-100 Hz as for 5-10 kHz.
    const lo = 45 * Math.pow(14000 / 45, i / BARS), hi = 45 * Math.pow(14000 / 45, (i + 1) / BARS);
    const a = Math.floor(lo / hz), z = Math.max(a + 1, Math.ceil(hi / hz));
    let sum = 0;
    for (let k = a; k < z; k++) sum += bins[k];
    const level = sum / (z - a) / 255 * (0.9 + 0.3 * i / BARS);   // highs are quieter: lift them a little
    out.push(Math.pow(Math.max(0, (level - 0.12) / 0.88), 1.5));
  }
  return out;
}

export function useMusic(
  on: boolean, tintHi: string,
  canvas: RefObject<HTMLCanvasElement | null>, glow: RefObject<HTMLDivElement | null>, art: RefObject<HTMLDivElement | null>,
) {
  const hiRef = useRef(tintHi);
  hiRef.current = tintHi;

  useEffect(() => {
    const g = canvas.current?.getContext('2d');
    if (!on || !g) return;
    diag.viz = true;
    const shown = new Array<number>(BARS).fill(0);
    const bins = analyser ? new Uint8Array(analyser.frequencyBinCount) : null;
    let last = 0, raf = 0, fill: CanvasGradient | null = null, fillFor = '';

    const frame = (t: number) => {
      raf = requestAnimationFrame(frame);
      if (t - last < 32) return;
      last = t;
      const levels = levelsNow(t, bins);
      for (let i = 0; i < BARS; i++) shown[i] = levels[i] > shown[i] ? levels[i] : Math.max(0, shown[i] - 0.03);

      const gap = 6, bw = (W - gap * (BARS + 1)) / BARS, hi = hiRef.current;
      if (hi !== fillFor || !fill) {
        const c = rgb(/^#[0-9a-f]{6}$/i.test(hi) ? hi : NEUTRAL.hi);
        fill = g.createLinearGradient(0, H, 0, 0);
        fill.addColorStop(0, `rgba(${c.join(',')},0.05)`);
        fill.addColorStop(1, `rgba(${c.join(',')},0.24)`);
        fillFor = hi;
      }
      g.clearRect(0, 0, W, H);
      g.fillStyle = fill;
      g.beginPath();
      for (let i = 0; i < BARS; i++) {
        const h = Math.max(3, shown[i] * (H - 6)), x = gap + i * (bw + gap);
        g.roundRect(x, H - h, bw, h + 4, [3, 3, 0, 0]);
      }
      g.fill();

      const bass = Math.min(1, (shown[0] + shown[1] + shown[2] + shown[3] + shown[4]) / 5 * 1.3);
      if (glow.current) {
        glow.current.style.opacity = (0.12 + bass * 0.5).toFixed(3);
        glow.current.style.transform = `scale(${(0.9 + bass * 0.16).toFixed(3)})`;
      }
      if (art.current) art.current.style.transform = `scale(${(1 + bass * 0.022).toFixed(4)})`;
    };
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      diag.viz = false;
      g.clearRect(0, 0, W, H);
      if (glow.current) glow.current.style.opacity = '0';
      if (art.current) art.current.style.transform = '';
    };
  }, [on, canvas, glow, art]);
}
