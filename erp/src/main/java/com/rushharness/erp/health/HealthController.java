package com.rushharness.erp.health;

import java.time.Instant;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Minimal liveness endpoint. This is NOT a business capability and must not be presented as one.
 */
@RestController
@RequestMapping("/api/erp/v1")
public class HealthController {

  @GetMapping("/health")
  public Map<String, Object> health() {
    return Map.of(
        "data", Map.of("status", "ok", "service", "procurement-erp"),
        "checked_at", Instant.now().toString());
  }
}
