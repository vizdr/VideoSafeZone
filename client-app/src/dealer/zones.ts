// Time zones for a new site: the browser's own first offered, the full list where the browser has one.
export const browserZone = (): string => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

export function timeZones(): string[] {
  try {
    return (Intl as unknown as { supportedValuesOf: (k: string) => string[] }).supportedValuesOf("timeZone");
  } catch {
    return [browserZone(), "UTC"];
  }
}
