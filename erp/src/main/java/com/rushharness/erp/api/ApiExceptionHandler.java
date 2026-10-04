package com.rushharness.erp.api;

import java.util.LinkedHashMap;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

/**
 * Renders every failure as {@code {"error": {"code", "message", "details"}, "request_id": ...}}.
 *
 * <p>Database failures become 503 DATABASE_UNAVAILABLE rather than an empty list: the plan forbids
 * degrading a broken dependency into a fake success.
 */
@RestControllerAdvice
public class ApiExceptionHandler {

  private static final Logger log = LoggerFactory.getLogger(ApiExceptionHandler.class);

  @ExceptionHandler(ApiException.class)
  public ResponseEntity<Map<String, Object>> handleApiException(ApiException failure) {
    return ResponseEntity.status(failure.status()).body(errorBody(failure));
  }

  @ExceptionHandler({
    MethodArgumentTypeMismatchException.class,
    MissingServletRequestParameterException.class,
    MethodArgumentNotValidException.class
  })
  public ResponseEntity<Map<String, Object>> handleBadRequest(Exception failure) {
    return ResponseEntity.status(HttpStatus.BAD_REQUEST)
        .body(
            errorBody(
                ApiException.invalidArgument(
                    "INVALID_ARGUMENT", failure.getMessage(), Map.of())));
  }

  @ExceptionHandler(DataAccessException.class)
  public ResponseEntity<Map<String, Object>> handleDatabaseFailure(DataAccessException failure) {
    log.error("database access failed", failure);
    return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
        .body(
            errorBody(
                ApiException.databaseUnavailable(
                    "database is unavailable; retry after the dependency recovers")));
  }

  @ExceptionHandler(Exception.class)
  public ResponseEntity<Map<String, Object>> handleUnexpected(Exception failure) {
    log.error("unhandled failure", failure);
    return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
        .body(
            errorBody(
                new ApiException(
                    HttpStatus.INTERNAL_SERVER_ERROR,
                    "INTERNAL_ERROR",
                    "unexpected server error",
                    Map.of())));
  }

  private static Map<String, Object> errorBody(ApiException failure) {
    Map<String, Object> error = new LinkedHashMap<>();
    error.put("code", failure.code());
    error.put("message", failure.getMessage());
    error.put("details", failure.details());

    Map<String, Object> body = new LinkedHashMap<>();
    body.put("error", error);
    body.put("request_id", RequestContext.currentRequestId());
    return body;
  }
}
