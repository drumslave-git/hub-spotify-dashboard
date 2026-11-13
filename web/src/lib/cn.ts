/** Joins class names, skipping the empty ones. */
export function cn(...names: (string | false | null | undefined)[]): string {
  return names.filter(Boolean).join(' ');
}
