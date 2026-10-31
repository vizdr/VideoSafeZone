// Whether this browser can decode H.265 is asked of the browser at runtime, never guessed from the
// user agent: it depends on the browser AND the machine. On one Windows PC only Chrome played
// H.265 while Firefox and Edge could not until Microsoft's "HEVC Video Extensions" were installed;
// on another PC that had them, all three did (measurements/codec-phase0.md §7). The probe string
// is the CODECS value KVS advertises.
export const HEVC_TYPE = 'video/mp4; codecs="hvc1.1.6.L90.0"';
export const HEVC_STORE_URL = "https://apps.microsoft.com/detail/9nmzlz57r3t7";

type MediaSourceLike = { isTypeSupported(type: string): boolean };

/** live: through hls.js (MSE) wherever that works, as the player does; clips: plain MP4 in <video>. */
export function hevcSupport(hlsSupported: boolean): { live: boolean; clips: boolean } {
  const w = window as unknown as { ManagedMediaSource?: MediaSourceLike; MediaSource?: MediaSourceLike };
  const MS = w.ManagedMediaSource || w.MediaSource;
  const mse = !!(MS && MS.isTypeSupported(HEVC_TYPE));
  const file = document.createElement("video").canPlayType(HEVC_TYPE) !== "";
  return { live: hlsSupported ? mse : file, clips: file };
}

/** Whether hls.js can play here -- its own isSupported() in short: Media Source Extensions with
 *  H.264 in fMP4. Lets the page decide without loading hls.js, which comes with the first player. */
export function mseSupported(): boolean {
  const w = window as unknown as { ManagedMediaSource?: MediaSourceLike; MediaSource?: MediaSourceLike };
  const MS = w.ManagedMediaSource || w.MediaSource;
  return !!(MS && MS.isTypeSupported('video/mp4; codecs="avc1.42E01E,mp4a.40.2"'));
}

/** On Windows the fix is an OS extension; elsewhere, another browser or device. */
export function hevcOnWindows(): boolean {
  return /Windows/.test(navigator.userAgent);
}
