package com.rushharness.erp.orders;

/**
 * One requested order line.
 *
 * <p>Boxed/typed deliberately: a missing field must be distinguishable from a zero value so the
 * caller gets a precise 400 instead of a silently defaulted order. {@code unitPrice} is a
 * two-decimal string, never a number.
 */
public record OrderLineRequest(String partId, Integer quantity, String unitPrice) {}
