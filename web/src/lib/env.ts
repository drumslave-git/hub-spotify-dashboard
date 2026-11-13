// The page's address settings, for trying things out:
//   ?demo=playing|plain|idle|night|setup  made-up player states, no server calls
//   ?at=HH:MM   the sky and the clock at another hour
//   ?wx=CODE    other weather (a WMO weather code)
//   ?player=1   play the sound on a desktop browser too
//   ?audio=element  use an <audio> element instead of Web Audio
//   ?cast=0     don't start the Cast receiver
export const params = new URLSearchParams(location.search);
export const demo = params.get('demo');
export const isCast = /CrKey/.test(navigator.userAgent);

/** ?at=HH:MM as [hour, minute], or null. */
export const at = (() => {
  const m = /^(\d\d?):(\d\d)$/.exec(params.get('at') || '');
  return m ? ([+m[1] % 24, +m[2]] as const) : null;
})();

export const loadedAt = Date.now();
