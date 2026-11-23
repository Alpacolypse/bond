/** A parsed JSON object (not an array or null), whose properties are of unknown type. */
export function isJsonObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
