package com.rushharness.erp.catalog;

/** A part offered by one supplier, including that supplier's catalogue price and lead time. */
public record SupplierPartView(
    String partId,
    String sku,
    String name,
    String unit,
    boolean active,
    String catalogPrice,
    String currency,
    int leadDays) {}
