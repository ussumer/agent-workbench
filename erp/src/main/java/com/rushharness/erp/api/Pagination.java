package com.rushharness.erp.api;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Validated pagination window. {@code page >= 1} and {@code 1 <= page_size <= 100} are hard
 * limits from contracts/erp.md; out-of-range input is a 400, never a silent clamp.
 */
public record Pagination(int page, int pageSize) {

  public static final int DEFAULT_PAGE_SIZE = 20;
  public static final int MAX_PAGE_SIZE = 100;

  public static Pagination of(Integer page, Integer pageSize) {
    int resolvedPage = page == null ? 1 : page;
    int resolvedSize = pageSize == null ? DEFAULT_PAGE_SIZE : pageSize;

    if (resolvedPage < 1) {
      throw ApiException.invalidArgument("INVALID_ARGUMENT", "page must be >= 1", detail("page", page));
    }
    if (resolvedSize < 1 || resolvedSize > MAX_PAGE_SIZE) {
      throw ApiException.invalidArgument(
          "INVALID_ARGUMENT",
          "page_size must be within 1.." + MAX_PAGE_SIZE,
          detail("page_size", pageSize));
    }
    return new Pagination(resolvedPage, resolvedSize);
  }

  public int offset() {
    return (page - 1) * pageSize;
  }

  private static Map<String, Object> detail(String key, Object value) {
    Map<String, Object> map = new LinkedHashMap<>();
    map.put(key, value);
    return map;
  }
}
