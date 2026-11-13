// The page as a Cast receiver app on the Hub. Registering as a receiver with
// the idle timeout disabled is what stops the Hub dropping back to its own
// ambient screen right away.
import { isCast, params } from './environment';
import { heartbeat, pageStatus } from './heartbeat';

type CastObject = Record<string, unknown>;
interface CastContext {
  addEventListener(type: unknown, listener: () => void): void;
  start(options: unknown): void;
  getPlayerManager(): { load(request: unknown): Promise<unknown> | void };
}
interface CastFramework {
  CastReceiverContext: { getInstance(): CastContext };
  CastReceiverOptions: new () => CastObject;
  ui: { UiConfig: new () => CastObject };
  system: { EventType: CastObject };
  messages: {
    LoadRequestData: new () => CastObject;
    MediaInformation: new () => CastObject;
    GenericMediaMetadata: new () => CastObject;
    StreamType: CastObject;
  };
}

const RECEIVER_SDK = 'https://www.gstatic.com/cast/sdk/libs/caf_receiver/v3/cast_receiver_framework.js';

// Even so, the Hub puts its home screen over an app that has no media loaded
// 10 minutes after it starts (and closes it 10 minutes later); the server then
// had to re-cast the page, with the Hub's cast chime each time. Home
// Assistant's receiver keeps a still image loaded in a hidden player and
// loads it again every 9 minutes; this does the same.
const RELOAD_IMAGE_EVERY = 9 * 60 * 1000;

function keepImageLoaded(framework: CastFramework, context: CastContext) {
  const hiddenPlayer = document.createElement('cast-media-player');
  hiddenPlayer.style.display = 'none';
  document.body.append(hiddenPlayer);
  const playerManager = context.getPlayerManager();
  const loadImage = () => {
    const failed = (error: unknown) => {
      pageStatus.media.error = String((error as { message?: string })?.message ?? JSON.stringify(error));
      heartbeat('media-failed: ' + pageStatus.media.error);
    };
    try {
      const media = new framework.messages.MediaInformation();
      media.contentId = new URL('keepalive.png', location.href).href;
      media.contentType = 'image/png';
      media.streamType = framework.messages.StreamType.NONE;
      const metadata = new framework.messages.GenericMediaMetadata();
      metadata.title = 'Kitchen display';
      media.metadata = metadata;
      const request = new framework.messages.LoadRequestData();
      request.autoplay = true;
      request.media = media;
      request.requestId = 0;
      Promise.resolve(playerManager.load(request)).then(() => {
        if (!pageStatus.media.loads++) heartbeat('media-loaded');
      }, failed);
    } catch (error) { failed(error); }
  };
  context.addEventListener(framework.system.EventType.READY, () => {
    loadImage();
    setInterval(loadImage, RELOAD_IMAGE_EVERY);
  });
}

// On a smart display the Cast system adds its own media-control layer over
// the page. It is invisible but would swallow every tap, and it can be added
// (or re-shown) at any time: keep it out of the way.
function hideCastTouchControls() {
  new MutationObserver(() => {
    document.querySelectorAll<HTMLElement>('touch-controls').forEach((element) => {
      element.style.setProperty('display', 'none', 'important');
    });
  }).observe(document.documentElement, { childList: true, subtree: true });
}

/** Starts the Cast receiver when the page runs on a Cast device. */
export function startCastReceiver() {
  if (!isCast) { pageStatus.castState = 'not-a-cast-device'; return; }
  if (params.get('cast') === '0') { pageStatus.castState = 'disabled'; return; }
  hideCastTouchControls();
  const script = document.createElement('script');
  script.src = RECEIVER_SDK;
  script.onload = () => {
    try {
      const framework = (window as unknown as { cast: { framework: CastFramework } }).cast.framework;
      const context = framework.CastReceiverContext.getInstance();
      const options = new framework.CastReceiverOptions();
      options.disableIdleTimeout = true;
      options.skipPlayersLoad = true;
      try {
        const uiConfig = new framework.ui.UiConfig();
        uiConfig.touchScreenOptimizedApp = true;
        options.uiConfig = uiConfig;
      } catch { /* older receiver framework */ }
      keepImageLoaded(framework, context);
      for (const name of ['READY', 'SHUTDOWN', 'SENDER_CONNECTED', 'SENDER_DISCONNECTED', 'ERROR']) {
        const eventType = framework.system.EventType[name];
        if (eventType) context.addEventListener(eventType, () => heartbeat('cast:' + name));
      }
      context.start(options);
      pageStatus.castState = 'started';
    } catch (error) {
      pageStatus.castState = 'error: ' + (error as Error)?.message;
    }
    heartbeat('cast-init:' + pageStatus.castState);
  };
  script.onerror = () => {
    pageStatus.castState = 'sdk-load-failed';
    heartbeat('cast-init:' + pageStatus.castState);
  };
  document.head.appendChild(script);
}
