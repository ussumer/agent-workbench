package com.rushharness.erp.catalog;

/** Part as exposed to the gateway. */
public record PartView(String partId, String sku, String name, String unit, boolean active) {}
