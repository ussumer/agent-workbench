package com.rushharness.erp.api;

import java.util.Map;
import org.springframework.http.HttpStatus;

/**
 * Business/protocol failure carrying the HTTP status and the machine-readable code from
 * contracts/erp.md, so handlers never have to guess.
 */
public class ApiException extends RuntimeException {

  private final HttpStatus status;
  private final String code;
  private final transient Map<String, Object> details;

  public ApiException(HttpStatus status, String code, String message, Map<String, Object> details) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details == null ? Map.of() : Map.copyOf(details);
  }

  public HttpStatus status() {
    return status;
  }

  public String code() {
    return code;
  }

  public Map<String, Object> details() {
    return details;
  }

  public static ApiException invalidArgument(String code, String message, Map<String, Object> details) {
    return new ApiException(HttpStatus.BAD_REQUEST, code, message, details);
  }

  public static ApiException notFound(String code, String message) {
    return new ApiException(HttpStatus.NOT_FOUND, code, message, Map.of());
  }

  public static ApiException unauthorized(String message) {
    return new ApiException(HttpStatus.UNAUTHORIZED, "UNAUTHORIZED", message, Map.of());
  }

  public static ApiException forbidden(String message) {
    return new ApiException(HttpStatus.FORBIDDEN, "FORBIDDEN", message, Map.of());
  }

  public static ApiException unprocessable(String code, String message) {
    return new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, code, message, Map.of());
  }

  public static ApiException conflict(String code, String message, Map<String, Object> details) {
    return new ApiException(HttpStatus.CONFLICT, code, message, details);
  }

  public static ApiException databaseUnavailable(String message) {
    return new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "DATABASE_UNAVAILABLE", message, Map.of());
  }
}
