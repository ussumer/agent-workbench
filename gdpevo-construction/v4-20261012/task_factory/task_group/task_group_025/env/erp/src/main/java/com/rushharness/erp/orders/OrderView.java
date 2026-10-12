package com.rushharness.erp.orders;

import java.time.Instant;
import java.util.List;

/** Complete order resource. {@code version} starts at 1 and increments on every successful edit. */
public record OrderView(
    String orderId,
    String orderNo,
    String ownerUserId,
    String supplierId,
    String currency,
    String totalAmount,
    int version,
    Instant createdAt,
    Instant updatedAt,
    String note,
    List<OrderLineView> lines) {}
