import { classNames } from '../lib/classNames';
import { STARS, TILES, type SkyLook } from '../lib/sky';

const STAR_SIZES = [
  'size-px',
  'size-[2px] [animation-duration:8.5s] [animation-delay:-3s]',
  'size-[1.5px] [animation-duration:4.5s] [animation-delay:-1s]',
];

const layer = 'absolute inset-x-0 -top-[300px] h-[900px] bg-repeat will-change-transform';

/** Behind the time and weather: the colours of the real sky at this hour,
 *  greyed by clouds, with stars at night and falling rain or snow. */
export function Sky({ look }: { look: SkyLook }) {
  const { glow, falling } = look;
  const rain = falling.startsWith('rain'), light = falling === 'rain light', snow = falling === 'snow';
  return (
    <div id="sky" className="sky absolute inset-0 overflow-hidden bg-bg" style={{ background: look.gradient }}>
      <div className="absolute top-0 left-0 -mt-[410px] -ml-[410px] size-[820px] rounded-full transition-opacity duration-[2s]"
           style={{
             background: `radial-gradient(closest-side, ${glow.color}, transparent)`,
             transform: `translate(${glow.x}px,${glow.y}px)`,
             opacity: glow.opacity.toFixed(2),
           }} />
      <div className="absolute top-0 left-0 h-[600px] w-[1024px] transition-opacity duration-[2s]"
           style={{ opacity: look.stars.toFixed(2) }}>
        {STARS.map((shadow, i) => (
          <i key={i} className={classNames('absolute top-0 left-0 animate-twinkle rounded-full', STAR_SIZES[i])} style={{ boxShadow: shadow }} />
        ))}
      </div>
      {(rain || snow) && (
        <div>
          <i className={classNames(layer, snow ? 'animate-snow1 opacity-85' : classNames('animate-rain1', light ? 'opacity-50' : 'opacity-90'))}
             style={{ backgroundImage: snow ? TILES.snow1 : TILES.rain1 }} />
          <i className={classNames(layer, snow ? 'animate-snow2 opacity-55' : classNames('animate-rain2', light ? 'opacity-30' : 'opacity-60'))}
             style={{ backgroundImage: snow ? TILES.snow2 : TILES.rain2 }} />
        </div>
      )}
    </div>
  );
}
