import { UserError } from "./errors.js";

// Same check as infra's laminar_users variable
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

// Trimmed and lowercased; Cognito treats emails case-insensitively
export function normalizeEmail(email: string): string {
  const clean = email.trim().toLowerCase();
  if (!EMAIL.test(clean)) throw new UserError(`"${email}" isn't an email address.`);
  return clean;
}

export function hasUser(users: string[], email: string): boolean {
  return users.some((user) => user.toLowerCase() === email);
}

export function withUser(users: string[], email: string): string[] {
  return hasUser(users, email) ? users : [...users, email];
}

// Refuses a missing user and the last one, so nobody gets locked out
export function withoutUser(users: string[], email: string): string[] {
  if (!hasUser(users, email)) {
    throw new UserError(`${email} isn't a user. See who is with \`tripwire users list\`.`);
  }
  if (users.length === 1) {
    throw new UserError(`${email} is the only user. Add someone else first.`);
  }
  return users.filter((user) => user.toLowerCase() !== email);
}