package com.rushharness.erp.inventory;

import com.rushharness.erp.api.ApiResponse;
import com.rushharness.erp.api.PageResponse;
import com.rushharness.erp.api.Pagination;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/erp/v1")
public class InventoryController {

  private final InventoryRepository repository;

  public InventoryController(InventoryRepository repository) {
    this.repository = repository;
  }

  @GetMapping("/inventory/warnings")
  public ApiResponse<PageResponse<InventoryWarning>> warnings(
      @RequestParam(name = "page", required = false) Integer page,
      @RequestParam(name = "page_size", required = false) Integer pageSize) {
    Pagination pagination = Pagination.of(page, pageSize);
    return ApiResponse.ok(repository.findWarnings(pagination));
  }
}
