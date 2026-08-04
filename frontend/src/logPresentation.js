export function getLogLevel(line) {
  if (/(?:^|[\s:])fatal\b|\b(exception|traceback)\b/i.test(line)) return "error";
  if (/\b(warning|warn)\b/i.test(line)) return "warning";
  if (/\b(error|failed)\b/i.test(line)) return "error";
  if (/^(blender quit|saved:|read blend:|time:)/i.test(line)) return "system";
  return "info";
}
