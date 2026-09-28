/**
 * Centralized Validation Functions
 *
 * This file serves as the single source of truth for all validation logic used across the application.
 * Validators are organized by field type for easy management and maintenance.
 *
 * Usage:
 * import { validators } from '@/constants/validators';
 * Then use: validators.email(email), validators.password(password), etc.
 */

import { REGEX } from './regex';

// ── Validation Result Type ────────────────────────────────────────────────────

export interface ValidationResult {
  isValid: boolean;
  error?: string;
}

// ── Validators ────────────────────────────────────────────────────────────────

export const validators = {
  // ── Email ─────────────────────────────────────────────────────────────────
  email: (value: string): ValidationResult => {
    const trimmed = value.trim();
    if (!trimmed) {
      return { isValid: false, error: 'Email is required.' };
    }
    if (!REGEX.EMAIL.test(trimmed)) {
      return { isValid: false, error: 'Enter a valid email address.' };
    }
    return { isValid: true };
  },

  // ── Password ──────────────────────────────────────────────────────────────
  password: (value: string, minLength: number = 8): ValidationResult => {
    if (!value) {
      return { isValid: false, error: 'Password is required.' };
    }
    if (value.length < minLength) {
      return {
        isValid: false,
        error: `Password must be at least ${minLength} characters.`,
      };
    }
    return { isValid: true };
  },

  passwordStrong: (value: string): ValidationResult => {
    if (!value) {
      return { isValid: false, error: 'Password is required.' };
    }
    if (!REGEX.PASSWORD_STRONG.test(value)) {
      return {
        isValid: false,
        error:
          'Password must contain at least one uppercase letter, one lowercase letter, one number, and be at least 8 characters long.',
      };
    }
    return { isValid: true };
  },

  // ── One-Time Code ─────────────────────────────────────────────────────────
  otpCode: (value: string): ValidationResult => {
    const trimmed = value.trim();
    if (!trimmed) {
      return { isValid: false, error: 'Reset code is required.' };
    }
    if (!REGEX.OTP_CODE.test(trimmed)) {
      return { isValid: false, error: 'Enter the 6-digit code from your email.' };
    }
    return { isValid: true };
  },

  // ── String Length ─────────────────────────────────────────────────────────
  stringLength: (
    value: string,
    min: number = 1,
    max?: number,
    fieldName: string = 'Field'
  ): ValidationResult => {
    const trimmed = value.trim();
    if (trimmed.length < min) {
      return {
        isValid: false,
        error: `${fieldName} must be at least ${min} characters.`,
      };
    }
    if (max && trimmed.length > max) {
      return {
        isValid: false,
        error: `${fieldName} must not exceed ${max} characters.`,
      };
    }
    return { isValid: true };
  },

  // ── Required Field ────────────────────────────────────────────────────────
  required: (value: string, fieldName: string = 'Field'): ValidationResult => {
    if (!value.trim()) {
      return { isValid: false, error: `${fieldName} is required.` };
    }
    return { isValid: true };
  },

  // ── Phone Number ──────────────────────────────────────────────────────────
  phone: (value: string): ValidationResult => {
    if (!value.trim()) {
      return { isValid: false, error: 'Phone number is required.' };
    }
    if (!REGEX.PHONE.test(value)) {
      return { isValid: false, error: 'Enter a valid phone number.' };
    }
    return { isValid: true };
  },

  // ── Integer ───────────────────────────────────────────────────────────────
  integer: (
    value: string | number,
    min?: number,
    max?: number
  ): ValidationResult => {
    const numValue = typeof value === 'string' ? parseInt(value, 10) : value;
    if (isNaN(numValue) || !REGEX.INTEGER.test(String(numValue))) {
      return { isValid: false, error: 'Must be a valid integer.' };
    }
    if (min !== undefined && numValue < min) {
      return {
        isValid: false,
        error: `Value must be at least ${min}.`,
      };
    }
    if (max !== undefined && numValue > max) {
      return {
        isValid: false,
        error: `Value must not exceed ${max}.`,
      };
    }
    return { isValid: true };
  },

  // ── Decimal Number ────────────────────────────────────────────────────────
  decimal: (
    value: string | number,
    min?: number,
    max?: number
  ): ValidationResult => {
    const numValue = typeof value === 'string' ? parseFloat(value) : value;
    if (isNaN(numValue) || !REGEX.DECIMAL.test(String(numValue))) {
      return { isValid: false, error: 'Must be a valid decimal number.' };
    }
    if (min !== undefined && numValue < min) {
      return {
        isValid: false,
        error: `Value must be at least ${min}.`,
      };
    }
    if (max !== undefined && numValue > max) {
      return {
        isValid: false,
        error: `Value must not exceed ${max}.`,
      };
    }
    return { isValid: true };
  },

  // ── URL ───────────────────────────────────────────────────────────────────
  url: (value: string): ValidationResult => {
    const trimmed = value.trim();
    if (!trimmed) {
      return { isValid: false, error: 'URL is required.' };
    }
    if (!REGEX.URL.test(trimmed)) {
      return { isValid: false, error: 'Enter a valid URL.' };
    }
    return { isValid: true };
  },

  // ── Username ──────────────────────────────────────────────────────────────
  username: (value: string): ValidationResult => {
    if (!value.trim()) {
      return { isValid: false, error: 'Username is required.' };
    }
    if (!REGEX.USERNAME.test(value)) {
      return {
        isValid: false,
        error:
          'Username must be 3-20 characters and contain only letters, numbers, underscores, and hyphens.',
      };
    }
    return { isValid: true };
  },

  // ── Name ──────────────────────────────────────────────────────────────────
  name: (value: string): ValidationResult => {
    const trimmed = value.trim();
    if (!trimmed) {
      return { isValid: false, error: 'Name is required.' };
    }
    if (!REGEX.NAME.test(trimmed)) {
      return {
        isValid: false,
        error:
          'Name must be at least 2 characters and contain only letters, spaces, hyphens, and apostrophes.',
      };
    }
    return { isValid: true };
  },

  // ── UUID ──────────────────────────────────────────────────────────────────
  uuid: (value: string): ValidationResult => {
    if (!value.trim()) {
      return { isValid: false, error: 'ID is required.' };
    }
    if (!REGEX.UUID.test(value)) {
      return { isValid: false, error: 'Invalid UUID format.' };
    }
    return { isValid: true };
  },

  // ── Date (ISO format: YYYY-MM-DD) ─────────────────────────────────────────
  dateISO: (value: string): ValidationResult => {
    if (!value.trim()) {
      return { isValid: false, error: 'Date is required.' };
    }
    if (!REGEX.DATE_ISO.test(value)) {
      return { isValid: false, error: 'Enter a valid date (YYYY-MM-DD).' };
    }
    return { isValid: true };
  },

  // ── Hex Color ─────────────────────────────────────────────────────────────
  hexColor: (value: string): ValidationResult => {
    if (!value.trim()) {
      return { isValid: false, error: 'Color is required.' };
    }
    if (!REGEX.HEX_COLOR.test(value)) {
      return { isValid: false, error: 'Enter a valid hex color.' };
    }
    return { isValid: true };
  },

  // ── Slug ──────────────────────────────────────────────────────────────────
  slug: (value: string): ValidationResult => {
    if (!value.trim()) {
      return { isValid: false, error: 'Slug is required.' };
    }
    if (!REGEX.SLUG.test(value)) {
      return {
        isValid: false,
        error:
          'Slug must contain only lowercase letters, numbers, and hyphens.',
      };
    }
    return { isValid: true };
  },

  // ── Custom Regex Pattern ──────────────────────────────────────────────────
  pattern: (
    value: string,
    pattern: RegExp,
    errorMessage: string = 'Invalid format.'
  ): ValidationResult => {
    if (!value.trim()) {
      return { isValid: false, error: 'Field is required.' };
    }
    if (!pattern.test(value)) {
      return { isValid: false, error: errorMessage };
    }
    return { isValid: true };
  },

  // ── Match Two Fields (Password Confirmation, etc.) ────────────────────────
  match: (
    value1: string,
    value2: string,
    fieldName: string = 'Fields'
  ): ValidationResult => {
    if (value1 !== value2) {
      return { isValid: false, error: `${fieldName} do not match.` };
    }
    return { isValid: true };
  },
} as const;
