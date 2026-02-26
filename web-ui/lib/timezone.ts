"use client";

type DateInput = string | number | Date | null | undefined;

const DEFAULT_TIMEZONE = (process.env.NEXT_PUBLIC_UA_DISPLAY_TIMEZONE || "America/Chicago").trim() || "America/Chicago";

function parseDateInput(value: DateInput): Date | null {
  if (value === null || value === undefined || value === "") return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;

  if (typeof value === "number") {
    const millis = Math.abs(value) >= 1_000_000_000_000 ? value : value * 1000;
    const parsed = new Date(millis);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  const raw = String(value).trim();
  if (!raw) return null;

  const numeric = Number(raw);
  if (Number.isFinite(numeric) && raw !== "") {
    const millis = Math.abs(numeric) >= 1_000_000_000_000 ? numeric : numeric * 1000;
    const parsed = new Date(millis);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  let normalized = raw;
  const hasExplicitZone = /(?:Z|[+\-]\d{2}:\d{2})$/i.test(normalized);
  const hasTime = /^\d{4}-\d{2}-\d{2}T/.test(normalized);
  if (hasTime && !hasExplicitZone) {
    // Backward-compatibility: legacy server timestamps were naive but represented UTC.
    normalized = `${normalized}Z`;
  }

  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function getDisplayTimezone(): string {
  return DEFAULT_TIMEZONE;
}

export function toEpochMs(value: DateInput): number | null {
  const parsed = parseDateInput(value);
  return parsed ? parsed.getTime() : null;
}

export function formatDateTimeTz(
  value: DateInput,
  options?: {
    timeZone?: string;
    dateStyle?: "full" | "long" | "medium" | "short";
    timeStyle?: "full" | "long" | "medium" | "short";
    placeholder?: string;
  },
): string {
  const parsed = parseDateInput(value);
  if (!parsed) return options?.placeholder ?? "--";
  return new Intl.DateTimeFormat("en-US", {
    timeZone: options?.timeZone || DEFAULT_TIMEZONE,
    dateStyle: options?.dateStyle || "medium",
    timeStyle: options?.timeStyle || "short",
  }).format(parsed);
}

export function formatTimeTz(
  value: DateInput,
  options?: {
    timeZone?: string;
    includeSeconds?: boolean;
    placeholder?: string;
  },
): string {
  const parsed = parseDateInput(value);
  if (!parsed) return options?.placeholder ?? "--:--:--";
  return new Intl.DateTimeFormat("en-US", {
    timeZone: options?.timeZone || DEFAULT_TIMEZONE,
    hour: "2-digit",
    minute: "2-digit",
    second: options?.includeSeconds === false ? undefined : "2-digit",
    hour12: false,
  }).format(parsed);
}

export function formatDateKeyTz(value: DateInput, timeZone?: string): string {
  const parsed = parseDateInput(value);
  if (!parsed) return "";
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: timeZone || DEFAULT_TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(parsed);
}
