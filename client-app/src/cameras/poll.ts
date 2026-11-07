// Asking again until an answer is the one awaited (DEALER-PANEL-PLAN.md D16): the adapter answers a
// scan or a camera request over MQTT, and the page reads the answer from the cloud, every few
// seconds, for as long as it is worth waiting.
export type Polled<T> = T | "timeout" | "stopped";

export async function pollUntil<T>(read: () => Promise<T | null>, done: (value: T) => boolean,
                                   opts: { every: number; within: number; stopped?: () => boolean }): Promise<Polled<T>> {
  const until = Date.now() + opts.within;
  for (;;) {
    if (opts.stopped?.()) return "stopped";
    const value = await read().catch(() => null);
    if (value !== null && done(value)) return value;
    if (Date.now() + opts.every > until) return "timeout";
    await new Promise(resolve => setTimeout(resolve, opts.every));
  }
}
