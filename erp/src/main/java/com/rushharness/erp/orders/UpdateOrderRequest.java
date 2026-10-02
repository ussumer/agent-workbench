package com.rushharness.erp.orders;

import java.util.List;

/**
 * Whole-order replacement. {@code expected_version} is mandatory: a fuzzy patch is not supported,
 * and the version is what makes two competing edits collide instead of overwriting each other.
 */
public record UpdateOrderRequest(
    Integer expectedVersion,
    String supplierId,
    String currency,
    List<OrderLineRequest> lines,
    String note) {}
