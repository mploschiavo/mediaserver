#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-media-stack}"

if command -v microk8s >/dev/null 2>&1; then
  KUBECTL=(microk8s kubectl)
elif command -v kubectl >/dev/null 2>&1; then
  KUBECTL=(kubectl)
else
  echo "[ERR] Neither microk8s nor kubectl is available in PATH." >&2
  exit 1
fi

secret_name="media-stack-secrets"
admin_user="$("${KUBECTL[@]}" -n "$NAMESPACE" get secret "$secret_name" -o jsonpath='{.data.STACK_ADMIN_USERNAME}' | base64 -d)"
admin_pass="$("${KUBECTL[@]}" -n "$NAMESPACE" get secret "$secret_name" -o jsonpath='{.data.STACK_ADMIN_PASSWORD}' | base64 -d)"

if [[ -z "${admin_user}" || -z "${admin_pass}" ]]; then
  echo "[ERR] STACK_ADMIN_USERNAME / STACK_ADMIN_PASSWORD are missing in secret ${NAMESPACE}/${secret_name}." >&2
  exit 1
fi

pod="$("${KUBECTL[@]}" -n "$NAMESPACE" get pod -l app=jellyseerr -o jsonpath='{.items[0].metadata.name}')"
if [[ -z "$pod" ]]; then
  echo "[ERR] Could not find jellyseerr pod in namespace ${NAMESPACE}." >&2
  exit 1
fi

user_b64="$(printf '%s' "$admin_user" | base64 | tr -d '\n')"
pass_b64="$(printf '%s' "$admin_pass" | base64 | tr -d '\n')"

echo "[INFO] Seeding Jellyseerr local admin in pod ${pod} (email/username: ${admin_user})"
"${KUBECTL[@]}" -n "$NAMESPACE" exec "$pod" -- sh -lc "cd /app && ADMIN_USER_B64='${user_b64}' ADMIN_PASS_B64='${pass_b64}' node - <<'NODE'
const sqlite3 = require('sqlite3').verbose();
const bcrypt = require('bcrypt');

const decode = (v) => Buffer.from(v || '', 'base64').toString('utf8');
const email = decode(process.env.ADMIN_USER_B64);
const password = decode(process.env.ADMIN_PASS_B64);
const username = email;

if (!email || !password) {
  console.error('missing admin credentials');
  process.exit(2);
}

const db = new sqlite3.Database('/app/config/db/db.sqlite3');
const run = (sql, params = []) =>
  new Promise((resolve, reject) =>
    db.run(sql, params, function (err) {
      if (err) reject(err);
      else resolve(this);
    })
  );
const get = (sql, params = []) =>
  new Promise((resolve, reject) =>
    db.get(sql, params, (err, row) => {
      if (err) reject(err);
      else resolve(row);
    })
  );

(async () => {
  try {
    await run('BEGIN IMMEDIATE TRANSACTION');
    const hash = await bcrypt.hash(password, 12);
    const existing = await get(
      'SELECT id, email, username FROM user WHERE lower(email)=lower(?) OR lower(username)=lower(?) ORDER BY id LIMIT 1',
      [email, username]
    );

    let userId;
    if (existing) {
      userId = existing.id;
      await run(
        \"UPDATE user SET email=?, username=?, password=?, permissions=(permissions | 2), userType=2, avatar=COALESCE(avatar,''), updatedAt=datetime('now') WHERE id=?\",
        [email, username, hash, userId]
      );
      console.log('updated user id=' + userId + ' email=' + email);
    } else {
      const ins = await run(
        \"INSERT INTO user (email, username, permissions, avatar, password, userType, createdAt, updatedAt) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))\",
        [email, username, 2, '', hash, 2]
      );
      userId = ins.lastID;
      console.log('created user id=' + userId + ' email=' + email);
    }

    const userSettings = await get('SELECT id FROM user_settings WHERE userId=?', [userId]);
    if (!userSettings) {
      await run(\"INSERT INTO user_settings (locale, userId) VALUES ('en', ?)\", [userId]);
      console.log('created user_settings for user id=' + userId);
    }

    await run('COMMIT');
    console.log('seed complete');
  } catch (err) {
    try {
      await run('ROLLBACK');
    } catch (_) {}
    console.error('seed failed:', err && err.message ? err.message : err);
    process.exit(1);
  } finally {
    db.close();
  }
})();
NODE"

echo "[INFO] Restarting jellyseerr deployment to ensure settings are reloaded"
"${KUBECTL[@]}" -n "$NAMESPACE" rollout restart deployment/jellyseerr >/dev/null
"${KUBECTL[@]}" -n "$NAMESPACE" rollout status deployment/jellyseerr --timeout=180s
echo "[OK] Jellyseerr local admin is seeded and deployment is ready."
