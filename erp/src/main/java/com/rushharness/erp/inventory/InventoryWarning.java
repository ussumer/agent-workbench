package com.rushharness.erp.inventory;

/**
 * One inventory warning row.
 *
 * <p>{@code suggestedQuantity} is reported only for rows that satisfy {@code on_hand <
 * warning_threshold}; a part may sit below {@code targetStock} without being warned.
 */
public record InventoryWarning(
    String partId,
    String sku,
    String name,
    int onHand,
    int warningThreshold,
    int targetStock,
    int suggestedQuantity) {}
