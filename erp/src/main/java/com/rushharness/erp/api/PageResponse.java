package com.rushharness.erp.api;

import java.util.List;

/**
 * Collection envelope: {@code {"items": [], "total": 0, "page": 1, "page_size": 20}}.
 *
 * @param total total number of matching rows, not the number returned on this page
 */
public record PageResponse<T>(List<T> items, long total, int page, int pageSize) {

  public static <T> PageResponse<T> of(List<T> items, long total, Pagination pagination) {
    return new PageResponse<>(items, total, pagination.page(), pagination.pageSize());
  }
}
