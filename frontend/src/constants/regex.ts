/**
 * Centralized Regex Patterns Configuration
 *
 * This file serves as the single source of truth for all regex patterns used across the application.
 * Patterns are organized by category for easy management and maintenance.
 *
 * Usage:
 * import { REGEX } from '@/constants/regex';
 * Then reference: REGEX.EMAIL, REGEX.PASSWORD, etc.
 */

export const REGEX = {
  // ── Email ─────────────────────────────────────────────────────────────────
  /** Matches valid email addresses */
  EMAIL: /^[^\s@]+@[^\s@]+\.[^\s@]+$/,

  // ── Password ──────────────────────────────────────────────────────────────
  /** At least 8 characters, one uppercase, one lowercase, one digit */
  PASSWORD_STRONG: /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$/,

  /** At least 8 characters */
  PASSWORD_BASIC: /^.{8,}$/,

  // ── One-Time Code ─────────────────────────────────────────────────────────
  /** 6-digit numeric verification/reset code */
  OTP_CODE: /^\d{6}$/,

  // ── URL ───────────────────────────────────────────────────────────────────
  /** Matches valid URLs (http/https) */
  URL: /^https?:\/\/(www\.)?[-a-zA-Z0-9@:%._+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_+.~#?&//=]*)$/,

  // ── Phone ─────────────────────────────────────────────────────────────────
  /** Basic phone number pattern (international format) */
  PHONE: /^[+]?[(]?[0-9]{1,4}[)]?[-\s.]?[(]?[0-9]{1,4}[)]?[-\s.]?[0-9]{1,9}$/,

  // ── Names ─────────────────────────────────────────────────────────────────
  /** Alphanumeric with spaces, hyphens, and apostrophes */
  NAME: /^[a-zA-Z\s'-]{2,}$/,

  // ── Username ──────────────────────────────────────────────────────────────
  /** Alphanumeric, underscores, hyphens, 3-20 characters */
  USERNAME: /^[a-zA-Z0-9_-]{3,20}$/,

  // ── Slug ──────────────────────────────────────────────────────────────────
  /** URL-safe slug format (lowercase letters, numbers, hyphens) */
  SLUG: /^[a-z0-9]+(?:-[a-z0-9]+)*$/,

  // ── Hex Color ─────────────────────────────────────────────────────────────
  /** Matches 3 or 6 digit hex colors */
  HEX_COLOR: /^#?([a-fA-F0-9]{6}|[a-fA-F0-9]{3})$/,

  // ── File Names ────────────────────────────────────────────────────────────
  /** Matches valid filenames (with extension) */
  FILENAME: /^[a-zA-Z0-9._-]+\.[a-zA-Z0-9]{1,5}$/,

  // ── Whitespace ────────────────────────────────────────────────────────────
  /** Matches leading and trailing whitespace */
  WHITESPACE_TRIM: /^\s+|\s+$/g,

  // ── Numbers ───────────────────────────────────────────────────────────────
  /** Matches whole numbers (integers) */
  INTEGER: /^-?\d+$/,

  /** Matches decimal numbers */
  DECIMAL: /^-?\d+(\.\d+)?$/,

  // ── UUID ──────────────────────────────────────────────────────────────────
  /** Matches UUID v4 format */
  UUID: /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,

  // ── Date Formats ──────────────────────────────────────────────────────────
  /** Matches YYYY-MM-DD format */
  DATE_ISO: /^\d{4}-\d{2}-\d{2}$/,

  /** Matches DD/MM/YYYY format */
  DATE_DMY: /^(0[1-9]|[12][0-9]|3[01])\/(0[1-9]|1[0-2])\/\d{4}$/,

  /** Matches MM/DD/YYYY format */
  DATE_MDY: /^(0[1-9]|1[0-2])\/(0[1-9]|[12][0-9]|3[01])\/\d{4}$/,
} as const;
