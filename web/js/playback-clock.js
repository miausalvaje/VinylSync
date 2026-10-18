/**
 * Local copy of a server clock field (e.g. elapsed_seconds), advancing at the
 * turntable speed the server measured between state events.
 */
export function createPlaybackClock(field) {
  let position = 0;
  let at = performance.now() / 1000;
  let rate = 1;

  return {
    // Only messages that carry the field move the clock: partial events
    // (capturer, error) leave a stale value in the merged state.
    update(payload) {
      if (payload[field] === undefined) {
        return false;
      }
      position = Number(payload[field] || 0);
      at = performance.now() / 1000;
      rate = Number(payload.playback_rate) || 1;
      return true;
    },
    now() {
      return position + (performance.now() / 1000 - at) * rate;
    },
  };
}
