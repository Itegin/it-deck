// Whether a token can travel in an HTTP header. fetch() throws before
// sending on anything outside Latin-1, and real tokens are narrower still:
// the Access dialog allows only printable ASCII. Kept apart from studio.js so
// it can be tested without a DOM.
export function headerSafeToken(token) {
  return typeof token === "string" && /^[\x21-\x7e]+$/.test(token);
}
