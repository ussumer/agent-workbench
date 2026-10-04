package com.rushharness.erp.catalog;

/** Supplier as exposed to the gateway (JSON is snake_case). */
public record SupplierView(String supplierId, String name, boolean active, String contact) {}
