package com.rushharness.erp.orders;

/** Persisted order line as returned to the gateway. Amounts are two-decimal strings. */
public record OrderLineView(
    int lineNo, String partId, int quantity, String unitPrice, String lineAmount) {}
