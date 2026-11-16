export function filterCommands(commands, query, limit = 12) {
  const needle = String(query ?? "").trim().toLocaleLowerCase();
  const maximum = Number.isInteger(limit) && limit > 0 ? limit : 12;
  if (!needle) return commands.slice(0, maximum);
  return commands.filter((command) => {
    const searchable = [command.label, ...(command.keywords ?? [])]
      .filter(Boolean)
      .join(" ")
      .toLocaleLowerCase();
    return searchable.includes(needle);
  }).slice(0, maximum);
}

export function isCommandPaletteShortcut({ key, ctrlKey, metaKey, targetTag }) {
  const tag = String(targetTag ?? "").toUpperCase();
  if (["INPUT", "TEXTAREA", "SELECT"].includes(tag)) return false;
  if (key === "/" && !ctrlKey && !metaKey) return true;
  return key.toLowerCase() === "k" && (ctrlKey || metaKey);
}
