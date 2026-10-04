package com.rushharness.erp.catalog;

/**
 * One supplier's offer for a part.
 *
 * <p>{@code catalogPrice} is a two-decimal string, never a float: money must survive JSON without
 * binary rounding.
 */
public record SupplierOffer(
    String supplierId, String name, String catalogPrice, String currency, int leadDays) {}
