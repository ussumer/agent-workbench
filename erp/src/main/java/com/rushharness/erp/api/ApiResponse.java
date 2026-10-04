package com.rushharness.erp.api;

/**
 * Success envelope required by contracts/erp.md: {@code {"data": ..., "request_id": "..."}}.
 *
 * <p>Field names are rendered snake_case by the global Jackson naming strategy.
 */
public record ApiResponse<T>(T data, String requestId) {

  public static <T> ApiResponse<T> ok(T data) {
    return new ApiResponse<>(data, RequestContext.currentRequestId());
  }
}
