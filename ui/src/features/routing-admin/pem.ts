// Minimal client-side PEM/X.509 introspection for the TLS install
// dialog. We deliberately avoid a heavyweight ASN.1 / x509 dep here —
// the bundle budget is tight and the controller validates the cert
// server-side anyway. The goal is *just* enough parsing to surface
// "this looks like a real cert" feedback before the operator submits.
//
// What this implements:
//   - PEM-marker detection (-----BEGIN CERTIFICATE----- / -----END ...).
//   - PEM body base64 decode → DER.
//   - First-pass ASN.1 walk to locate the subject's CN string and the
//     `notBefore` / `notAfter` validity timestamps. Anything we can't
//     parse cleanly returns `undefined`; the UI displays a "could not
//     parse — server will validate" hint and lets the user submit.
//
// All inputs are treated as untrusted. We never throw — every helper
// returns `undefined` on malformed bytes so the dialog stays usable.

const CERT_BEGIN = "-----BEGIN CERTIFICATE-----";
const CERT_END = "-----END CERTIFICATE-----";

const KEY_BEGIN_RE = /-----BEGIN [A-Z0-9 ]+ ?KEY-----/;
const KEY_END_RE = /-----END [A-Z0-9 ]+ ?KEY-----/;

export function looksLikeCertPem(text: string): boolean {
  return text.includes(CERT_BEGIN) && text.includes(CERT_END);
}

export function looksLikeKeyPem(text: string): boolean {
  return KEY_BEGIN_RE.test(text) && KEY_END_RE.test(text);
}

function pemBodyToDer(pem: string): Uint8Array | undefined {
  const start = pem.indexOf(CERT_BEGIN);
  const end = pem.indexOf(CERT_END);
  if (start === -1 || end === -1 || end < start) return undefined;
  const body = pem
    .slice(start + CERT_BEGIN.length, end)
    .replace(/[\r\n\s]+/g, "");
  if (!body) return undefined;
  try {
    const bin = atob(body);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  } catch {
    return undefined;
  }
}

// Tiny ASN.1 cursor. Reads the leading TLV header and lets the caller
// walk the structure manually. We only support the shapes we actually
// need (SEQUENCE / SET / OBJECT IDENTIFIER / time strings / strings).
interface Tlv {
  tag: number;
  length: number;
  headerLength: number;
  contentStart: number;
}

function readTlv(buf: Uint8Array, off: number): Tlv | undefined {
  if (off >= buf.length) return undefined;
  const tag = buf[off];
  if (tag === undefined) return undefined;
  let i = off + 1;
  if (i >= buf.length) return undefined;
  const first = buf[i];
  if (first === undefined) return undefined;
  let length: number;
  if ((first & 0x80) === 0) {
    length = first;
    i += 1;
  } else {
    const numLen = first & 0x7f;
    if (numLen === 0 || numLen > 4) return undefined;
    if (i + 1 + numLen > buf.length) return undefined;
    length = 0;
    for (let j = 0; j < numLen; j++) {
      const byte = buf[i + 1 + j];
      if (byte === undefined) return undefined;
      length = (length << 8) | byte;
    }
    i += 1 + numLen;
  }
  if (i + length > buf.length) return undefined;
  return { tag, length, headerLength: i - off, contentStart: i };
}

// Constants for the OIDs we care about. Encoded as DER content bytes
// (without the OID tag/length wrapper).
const OID_CN = new Uint8Array([0x55, 0x04, 0x03]); // 2.5.4.3

function bytesEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

function decodeUtf8(buf: Uint8Array): string {
  try {
    return new TextDecoder("utf-8", { fatal: false }).decode(buf);
  } catch {
    return "";
  }
}

/**
 * Parse a Y/M/D/H/M/S out of an ASN.1 UTCTime ("YYMMDDhhmmssZ") or
 * GeneralizedTime ("YYYYMMDDhhmmssZ"). Returns an ISO-8601 string,
 * or `undefined` if the bytes don't decode cleanly.
 */
function parseAsn1Time(tag: number, bytes: Uint8Array): string | undefined {
  const s = decodeUtf8(bytes);
  if (!s.endsWith("Z")) return undefined;
  const body = s.slice(0, -1);
  let yyyy: number, mm: number, dd: number, hh: number, mi: number, ss: number;
  if (tag === 0x17 && body.length >= 10) {
    // UTCTime YYMMDDhhmm[ss]
    const yy = Number(body.slice(0, 2));
    yyyy = yy < 50 ? 2000 + yy : 1900 + yy;
    mm = Number(body.slice(2, 4));
    dd = Number(body.slice(4, 6));
    hh = Number(body.slice(6, 8));
    mi = Number(body.slice(8, 10));
    ss = body.length >= 12 ? Number(body.slice(10, 12)) : 0;
  } else if (tag === 0x18 && body.length >= 14) {
    // GeneralizedTime YYYYMMDDhhmmss
    yyyy = Number(body.slice(0, 4));
    mm = Number(body.slice(4, 6));
    dd = Number(body.slice(6, 8));
    hh = Number(body.slice(8, 10));
    mi = Number(body.slice(10, 12));
    ss = Number(body.slice(12, 14));
  } else {
    return undefined;
  }
  if ([yyyy, mm, dd, hh, mi, ss].some((n) => !Number.isFinite(n))) {
    return undefined;
  }
  const d = new Date(Date.UTC(yyyy, mm - 1, dd, hh, mi, ss));
  if (Number.isNaN(d.getTime())) return undefined;
  return d.toISOString();
}

export interface ParsedCertSummary {
  subjectCn?: string;
  validFrom?: string;
  validTo?: string;
}

/**
 * Best-effort parse of a PEM-encoded X.509 cert. We descend into the
 * outer TBS SEQUENCE looking for two things: the validity SEQUENCE
 * (two times) and a SET inside the subject SEQUENCE that contains a
 * CN-tagged AttributeTypeAndValue. Both are optional — anything we
 * can't extract returns `undefined` and the UI shows "—".
 */
export function parseCertPem(pem: string): ParsedCertSummary {
  const der = pemBodyToDer(pem);
  if (!der) return {};

  // Outer: SEQUENCE { tbsCertificate, sigAlg, sigValue }
  const outer = readTlv(der, 0);
  if (!outer || outer.tag !== 0x30) return {};
  const tbs = readTlv(der, outer.contentStart);
  if (!tbs || tbs.tag !== 0x30) return {};

  // Walk TBS children. The structure is roughly:
  //   [0] EXPLICIT version (optional)
  //   INTEGER serial
  //   SEQUENCE signature
  //   SEQUENCE issuer
  //   SEQUENCE validity { time, time }
  //   SEQUENCE subject
  //   ...
  let cursor = tbs.contentStart;
  const tbsEnd = tbs.contentStart + tbs.length;
  const seqs: Tlv[] = [];
  while (cursor < tbsEnd) {
    const t = readTlv(der, cursor);
    if (!t) break;
    if (t.tag === 0x30) seqs.push(t);
    cursor = t.contentStart + t.length;
  }

  const summary: ParsedCertSummary = {};

  // Validity is the first SEQUENCE whose contents are exactly two
  // ASN.1 time values.
  for (const seq of seqs) {
    const a = readTlv(der, seq.contentStart);
    if (!a) continue;
    if (a.tag !== 0x17 && a.tag !== 0x18) continue;
    const b = readTlv(der, a.contentStart + a.length);
    if (!b) continue;
    if (b.tag !== 0x17 && b.tag !== 0x18) continue;
    summary.validFrom = parseAsn1Time(
      a.tag,
      der.slice(a.contentStart, a.contentStart + a.length),
    );
    summary.validTo = parseAsn1Time(
      b.tag,
      der.slice(b.contentStart, b.contentStart + b.length),
    );
    break;
  }

  // Subject CN: descend into each top-level SEQUENCE → SET → SEQUENCE
  // (AttributeTypeAndValue) and check for the CN OID.
  outer_loop: for (const seq of seqs) {
    let inner = seq.contentStart;
    const innerEnd = seq.contentStart + seq.length;
    while (inner < innerEnd) {
      const set = readTlv(der, inner);
      if (!set) break;
      if (set.tag === 0x31) {
        const atv = readTlv(der, set.contentStart);
        if (atv && atv.tag === 0x30) {
          const oid = readTlv(der, atv.contentStart);
          if (oid && oid.tag === 0x06 && oid.length === OID_CN.length) {
            const oidBytes = der.slice(
              oid.contentStart,
              oid.contentStart + oid.length,
            );
            if (bytesEqual(oidBytes, OID_CN)) {
              const value = readTlv(der, oid.contentStart + oid.length);
              if (value) {
                summary.subjectCn = decodeUtf8(
                  der.slice(value.contentStart, value.contentStart + value.length),
                );
                break outer_loop;
              }
            }
          }
        }
      }
      inner = set.contentStart + set.length;
    }
  }

  return summary;
}
