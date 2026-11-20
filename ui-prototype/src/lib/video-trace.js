export function resolveProductAttempt(product, attempts) {
  if (!product || !Array.isArray(attempts) || typeof product.initiative_id !== "string"
      || typeof product.version_id !== "string") {
    return { status: "missing", attemptId: null };
  }
  const matches = attempts.filter((attempt) =>
    attempt && attempt.product_id === product.initiative_id
      && attempt.version_id === product.version_id
      && typeof attempt.attempt_id === "string"
      && /^[A-Za-z0-9_-]{1,64}$/.test(attempt.attempt_id),
  );
  if (matches.length === 1) return { status: "matched", attemptId: matches[0].attempt_id };
  return { status: matches.length > 1 ? "ambiguous" : "missing", attemptId: null };
}
