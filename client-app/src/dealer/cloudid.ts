// The Cloud ID as a person types it (DEALER-PANEL-PLAN.md D15, §7.3): ten characters of Crockford
// base32, shown K7QM4-XRPZ9. The field upper-cases and adds the dash as you type; the server reads
// it the same way (normalize_cloud_id in cloud/lambda/vsz_directory.py): O is 0, I and L are 1.
const ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

/** What the field shows: upper case, letters and digits only, a dash after the fifth, ten at most. */
export function formatCloudId(typed: string): string {
  const chars = typed.toUpperCase().replace(/[^0-9A-Z]/g, "").slice(0, 10);
  return chars.length > 5 ? `${chars.slice(0, 5)}-${chars.slice(5)}` : chars;
}

/** The ten characters the server will read, or null when it can't be a Cloud ID. */
export function readCloudId(typed: string): string | null {
  const code = typed.toUpperCase().replace(/[\s-]/g, "").replace(/O/g, "0").replace(/[IL]/g, "1");
  return code.length === 10 && [...code].every(ch => ALPHABET.includes(ch)) ? code : null;
}
