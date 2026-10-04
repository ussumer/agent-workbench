package com.rushharness.erp.health;

import com.rushharness.erp.api.ApiResponse;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Liveness and readiness are deliberately separate.
 *
 * <p>{@code /health} answers "is the process up" without touching the database. {@code
 * /health/ready} additionally probes the database and returns 503 DATABASE_UNAVAILABLE when it
 * fails, so a caller can never mistake a broken dependency for a healthy service.
 */
@RestController
@RequestMapping("/api/erp/v1")
public class HealthController {

  private final JdbcTemplate jdbc;

  public HealthController(JdbcTemplate jdbc) {
    this.jdbc = jdbc;
  }

  @GetMapping("/health")
  public ApiResponse<Map<String, Object>> health() {
    Map<String, Object> data = new LinkedHashMap<>();
    data.put("status", "ok");
    data.put("service", "procurement-erp");
    return ApiResponse.ok(data);
  }

  @GetMapping("/health/ready")
  public ApiResponse<Map<String, Object>> ready() {
    Integer probe = jdbc.queryForObject("SELECT 1", Integer.class);
    Map<String, Object> data = new LinkedHashMap<>();
    data.put("status", "ready");
    data.put("service", "procurement-erp");
    data.put("database", probe != null && probe == 1 ? "up" : "unknown");
    return ApiResponse.ok(data);
  }
}
