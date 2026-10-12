package com.rushharness.erp.catalog;

import com.rushharness.erp.api.ApiException;
import com.rushharness.erp.api.ApiResponse;
import com.rushharness.erp.api.PageResponse;
import com.rushharness.erp.api.Pagination;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * Catalog read endpoints from contracts/erp.md.
 *
 * <p>Controllers only validate input and delegate: no seed array or business value is produced
 * here, every row comes from the database.
 */
@RestController
@RequestMapping("/api/erp/v1")
public class CatalogController {

  private final CatalogRepository repository;

  public CatalogController(CatalogRepository repository) {
    this.repository = repository;
  }

  @GetMapping("/suppliers")
  public ApiResponse<PageResponse<SupplierView>> suppliers(
      @RequestParam(name = "q", required = false) String q,
      @RequestParam(name = "active", required = false) Boolean active,
      @RequestParam(name = "page", required = false) Integer page,
      @RequestParam(name = "page_size", required = false) Integer pageSize) {
    Pagination pagination = Pagination.of(page, pageSize);
    return ApiResponse.ok(repository.findSuppliers(q, active, pagination));
  }

  @GetMapping("/parts")
  public ApiResponse<PageResponse<PartView>> parts(
      @RequestParam(name = "q", required = false) String q,
      @RequestParam(name = "page", required = false) Integer page,
      @RequestParam(name = "page_size", required = false) Integer pageSize) {
    Pagination pagination = Pagination.of(page, pageSize);
    return ApiResponse.ok(repository.findParts(q, pagination));
  }

  @GetMapping("/parts/{part_id}")
  public ApiResponse<PartDetail> part(@PathVariable("part_id") String partId) {
    PartView part =
        repository
            .findPart(partId)
            .orElseThrow(() -> ApiException.notFound("PART_NOT_FOUND", "no part with id " + partId));
    return ApiResponse.ok(new PartDetail(part, repository.findAvailableSuppliers(partId)));
  }

  @GetMapping("/suppliers/{supplier_id}/parts")
  public ApiResponse<PageResponse<SupplierPartView>> supplierParts(
      @PathVariable("supplier_id") String supplierId,
      @RequestParam(name = "page", required = false) Integer page,
      @RequestParam(name = "page_size", required = false) Integer pageSize) {
    if (!repository.supplierExists(supplierId)) {
      throw ApiException.notFound(
          "SUPPLIER_NOT_FOUND", "no supplier with id " + supplierId);
    }
    Pagination pagination = Pagination.of(page, pageSize);
    return ApiResponse.ok(repository.findPartsBySupplier(supplierId, pagination));
  }
}
