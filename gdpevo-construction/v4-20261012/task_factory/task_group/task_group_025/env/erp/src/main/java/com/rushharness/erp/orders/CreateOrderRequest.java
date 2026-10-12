package com.rushharness.erp.orders;

import java.util.List;

/**
 * Create-order body. {@code total_amount} is intentionally absent: the server computes money, and
 * an unknown property in the body is rejected rather than ignored.
 */
public record CreateOrderRequest(
    String supplierId, String currency, List<OrderLineRequest> lines, String note) {}
