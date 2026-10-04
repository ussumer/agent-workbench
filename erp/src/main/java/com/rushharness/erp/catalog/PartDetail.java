package com.rushharness.erp.catalog;

import java.util.List;

/** A part plus the active suppliers that can actually supply it. */
public record PartDetail(PartView part, List<SupplierOffer> availableSuppliers) {}
