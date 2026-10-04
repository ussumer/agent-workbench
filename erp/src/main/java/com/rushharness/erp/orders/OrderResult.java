package com.rushharness.erp.orders;

/**
 * Outcome of a write, plus whether it was served from the idempotency ledger.
 *
 * <p>{@code replayed=true} means the business write happened earlier and the stored response is
 * being returned; the controller advertises that with the {@code Idempotent-Replayed} header so a
 * caller can tell a fresh write from a safe retry.
 */
public record OrderResult(OrderView order, boolean replayed) {}
